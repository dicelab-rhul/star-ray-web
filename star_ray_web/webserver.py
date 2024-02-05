from fastapi import FastAPI
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles


import random
import asyncio
import time
import ray
from ray import serve
from ray.serve.schema import LoggingConfig

from pathlib import Path

import logging


from icua2.agent import Agent, Sensor, Actuator
from icua2.event import (
    Event,
    Response as ResponseEvent,
    VisibilityEvent,
    MouseButtonEvent,
    MouseMotionEvent,
)

app = FastAPI()
PATH = str(Path(__file__).resolve().parent)
templates = Jinja2Templates(directory=str(Path(PATH, "templates")))

ray.init()
serve.start(http_options={"port": 8000})


# This is a fake sensor that just returns some new svg code.
class _StubSensor(Sensor):
    def get(self):
        stub_response = ResponseEvent.new(
            Event.new(),
            success=True,
            data={
                "root": f"""<svg xmlns="http://www.w3.org/2000/svg"><circle cx="100" cy="100" r="50" fill="{self.random_color()}" onclick="handleMouseClick(event)" /></svg>"""
            },
        )
        return [stub_response]

    def random_color(self):
        # Generate random values for red, green, and blue components
        red = random.randint(0, 255)
        green = random.randint(0, 255)
        blue = random.randint(0, 255)
        # Convert the RGB components to a hexadecimal color string
        hex_color = "#{:02X}{:02X}{:02X}".format(red, green, blue)
        # Return the hexadecimal color string
        return hex_color


class ActuatorWeb(Actuator):
    @Actuator.action
    def attempt_mouse_click(self, web_event):
        print(web_event)
        return None


@serve.deployment(
    logging_config=LoggingConfig(enable_access_log=False, log_level="WARNING")
)
@serve.ingress(app)
class AvatarWebServer(Agent):
    def __init__(self, *args, **kwargs):
        # need to give self to super().__init__ due to decorators...
        super().__init__([_StubSensor()], [ActuatorWeb()], self, *args, **kwargs)
        self._svg_code = """<svg xmlns="http://www.w3.org/2000/svg"><circle cx="100" cy="100" r="50" fill="blue" onclick="handleMouseClick(event)" /></svg>"""
        self._svg_changed = asyncio.Event()
        self._logger = logging.getLogger("ray.serve")

    @app.get("/", response_class=HTMLResponse)
    async def index(self, request: Request) -> str:
        # in line these to avoid caching issues after modification
        with open(PATH + "/static/js/handle_input.js", "r") as js_file:
            handle_input = f"<script>{js_file.read()}</script>"
        with open(PATH + "/static/js/handle_visibility.js", "r") as js_file:
            handle_visibility = f"<script>{js_file.read()}</script>"

        response = templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "svg_code": self._svg_code,
                "handle_input": handle_input,
                "handle_visibility": handle_visibility,
            },
        )
        return response

    def __cycle__(self):
        self._svg_code = self.sensors[0].get()[0].data["root"]
        self._svg_changed.set()

    @app.post("/on_mouse_button")
    async def on_mouse_button(self, request: Request) -> str:
        try:
            data = await request.json()
            position = data["position"]
            button = data["button"]
            status = data["status"]
            target = data["id"]
            target = target if len(target) > 0 else None
            self._trigger_event(
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
            position = data["position"]
            relative = data["relative"]
            target = data["id"]
            target = target if len(target) > 0 else None
            self._trigger_event(
                MouseMotionEvent.new(
                    position=position, relative=relative, target=target
                )
            )
            return JSONResponse(content={})
        except Exception as e:
            self._logger.exception("Invalid post request.")
            return JSONResponse(content={"error": str(e)}, status_code=500)

    @app.post("/on_visibility_change")
    async def on_visibility_change(self, request: Request) -> str:
        try:
            data = await request.json()
            if data["visibility"] == "visible":
                # trigger an event
                self._trigger_event(VisibilityEvent.new_visible())
            elif data["visibility"] == "hidden":
                self._trigger_event(VisibilityEvent.new_hidden())
            else:
                raise ValueError(
                    f"Invalid value for `visibility` {data['visibility']}, valid values include: [`visible`, `hidden`]"
                )
            return JSONResponse(content={})
        except Exception as e:
            self._logger.exception("Invalid post request.")
            return JSONResponse(content={"error": str(e)}, status_code=500)

    @app.websocket("/svgsocket")
    async def update_svg(self, ws: WebSocket):
        await ws.accept()
        try:
            while True:
                # wait for the svg to be set
                await self._svg_changed.wait()
                self._svg_changed.clear()  #
                # send the svg data to the client
                await ws.send_text(self._svg_code)
        except WebSocketDisconnect:
            print("Client disconnected.")

    def _trigger_event(self, event):
        if not isinstance(event, MouseMotionEvent):
            print("TRIGGER: ", event)  # TODO
