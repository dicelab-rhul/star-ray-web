import asyncio
from importlib.resources import files
from pathlib import Path
import logging
from typing import Any, Dict, List, Tuple

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from ray import serve
from ray.serve.schema import LoggingConfig

from star_ray.event import (
    VisibilityEvent,
    MouseButtonEvent,
    MouseMotionEvent,
)

EVENT_BUFFER_MAX_SIZE = 10000

ROUTE_MOUSE_BUTTON = "on_mouse_button"
ROUTE_MOUSE_MOTION = "on_mouse_motion"
ROUTE_VISIBILITY_CHANGE = "on_visibility_change"


def get_default_template_data():
    """Gets the default Jinja2 template data for use with star_ray_web/static/templates/index.html.jinja

    Returns:
        Dict[str, Any]: default template data

    Usage Example:
    ```
    from ray import serve
    from star_ray_web import WebServer, get_default_template_data
    serve.start(http_options={"port": 8888})
    webserver = serve.run(WebServer.bind(template_data=get_default_template_data()))
    ```
    """
    return dict(
        handle_mouse_button=dict(
            post_route=ROUTE_MOUSE_BUTTON,
            disable_context_menu=True,
        ),
        handle_mouse_motion=dict(
            post_route=ROUTE_MOUSE_MOTION,
        ),
        handle_visibility=dict(post_route=ROUTE_VISIBILITY_CHANGE),
        head="",
        body="",
    )


class _EventBuffer(asyncio.Queue):
    """A custom asyncio.Queue for buffering events with a maximum size.

    Attributes:
        maxsize (int): The maximum size of the queue. Defaults to 0, indicating an infinite size.
    """

    async def put(self, item: Any):
        """Add an item to the queue, raising an error if the queue is full.

        Args:
            item (Any): The item to be added to the queue.

        Raises:
            IndexError: If attempting to add an item to a full queue.
        """
        if self.full():
            raise IndexError(f"Event buffer is full, failed to add item {item}.")
        await super().put(item)

    def get_all_nowait(self) -> List[Any]:
        """Retrieves all items from the buffer without blocking and empties the buffer.

        This method retrieves items synchronously and does not wait for items to become available,
        which may raise a [QueueEmpty] exception if the buffer is unexpectedly accessed asynchronously.

        Returns:
            List[Any]: A list of items retrieved from the buffer.
        """
        items = []
        while not self.empty():
            item = self.get_nowait()
            items.append(item)
            if len(items) >= self.maxsize:
                logging.getLogger("ray.serve").warning(
                    "returned early from event buffer `get_all`, the event buffer might be filling up to fast!",
                    stack_info=True,
                )
                return items
        return items


# fastapi app - this is globally defined singleton that is used by WebServer
app = FastAPI()


class WebSocketHandler:

    def __init__(self):
        super().__init__()
        self._message = None
        self._changed = asyncio.Event()
        self._closed = asyncio.Event()

    def update(self, message):
        self._message = message
        self._changed.set()

    def close(self):
        self._closed.set()

    async def __call__(self, websocket: WebSocket, *args):
        await websocket.accept()
        try:
            while not self._closed.is_set():
                await self._changed.wait()
                self._changed.clear()
                await websocket.send_text(self._message)
            websocket.close()
        except WebSocketDisconnect as e:
            raise e


@serve.deployment(
    num_replicas=1,
    logging_config=LoggingConfig(enable_access_log=False, log_level="WARNING"),
)
@serve.ingress(app)
class WebServer:
    def __init__(
        self,
        *args: Tuple[Any, ...],
        template_path: str = None,
        template_data: Dict[str, str] = None,
        **kwargs: Dict[str, Any],
    ):
        # need to give self to super().__init__ due to decorators... it looks a bit weird I know...
        super().__init__(self, *args, **kwargs)
        self._logger = logging.getLogger("ray.serve")
        self._socket_handlers = {}
        self._templates_path = (
            str(files(__package__).joinpath("static"))
            if template_path is None
            else template_path
        )
        print(self._templates_path)
        self._template_data = dict() if template_data is None else template_data
        self._event_buffer = _EventBuffer(maxsize=EVENT_BUFFER_MAX_SIZE)

    @app.get("/", response_class=HTMLResponse)
    async def index(self, request: Request) -> str:
        # in line javascript to avoid static file caching issues after modification
        templates = Jinja2Templates(directory=self._templates_path)
        response = templates.TemplateResponse(
            "templates/index.html.jinja",
            {"request": request, **self._template_data},
        )
        return response

    @app.post(f"/{ROUTE_MOUSE_BUTTON}")
    async def on_mouse_button(self, request: Request) -> str:
        try:
            data = await request.json()
            position = (data["position"]["x"], data["position"]["y"])
            button = data["button"]
            status = MouseButtonEvent.status_from_string(data["status"])
            target = data["id"]
            target = target if len(target) > 0 else None
            await self._add_event(
                MouseButtonEvent.new(
                    button=button, position=position, status=status, target=target
                )
            )
            return JSONResponse(content={})
        except Exception as e:
            self._logger.exception("Invalid post request.")
            return JSONResponse(content={"error": str(e)}, status_code=500)

    @app.post("/on_mouse_motion")
    async def on_mouse_motion(self, request: Request) -> str:
        try:
            data = await request.json()
            position = (data["position"]["x"], data["position"]["y"])
            relative = (data["relative"]["x"], data["relative"]["y"])
            target = data["id"]
            target = target if len(target) > 0 else None
            await self._add_event(
                MouseMotionEvent.new(
                    position=position, relative=relative, target=target
                )
            )
            return JSONResponse(content={})
        except Exception as e:
            self._logger.exception("Invalid post request.")
            return JSONResponse(content={"error": str(e)}, status_code=500)

    @app.post(f"/{ROUTE_VISIBILITY_CHANGE}")
    async def on_visibility_change(self, request: Request) -> str:
        try:
            data = await request.json()
            if data["visibility"] == "visible":
                # trigger an event
                await self._add_event(VisibilityEvent.new_visible())
            elif data["visibility"] == "hidden":
                await self._add_event(VisibilityEvent.new_hidden())
            else:
                raise ValueError(
                    f"Invalid value for `visibility` {data['visibility']}, valid values include: [`visible`, `hidden`]"
                )
            return JSONResponse(content={})
        except Exception as e:
            self._logger.exception("Invalid post request.")
            return JSONResponse(content={"error": str(e)}, status_code=500)

    @app.websocket("/{route_id}")
    async def _websocket_router(self, websocket: WebSocket, route_id: str):
        await self._socket_handlers[route_id](websocket)

    def open_socket(self, route):
        if route in self._socket_handlers.keys():
            self._socket_handlers[route].close()
            del self._socket_handlers[route]
        socket_handler = WebSocketHandler()
        self._socket_handlers[route] = socket_handler

    def update_socket(self, route, message):
        self._socket_handlers[route].update(message)

    async def _add_event(self, event):
        await self._event_buffer.put(event)

    def get_events(self):
        # this should be called remotely to pop from the event queue.
        return self._event_buffer.get_all_nowait()
