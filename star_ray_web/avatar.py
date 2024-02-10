# pylint: disable=no-member
from typing import List
from importlib.resources import files

from ray import serve
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from star_ray.agent import Agent, Sensor, Actuator
from star_ray.environment.xml.query_xml import QueryXML
from star_ray.event import Event

from .webserver import WebServer, get_default_template_data

DEFAULT_WEBSERVER_PORT = 8888
_TEMPLATES_PATH = files(__package__).joinpath("static/templates")
_JINJA2_ENV = Environment(
    loader=FileSystemLoader(_TEMPLATES_PATH), undefined=StrictUndefined
)
DEFAULT_SVG_CODE = """<svg id="root" xmlns="http://www.w3.org/2000/svg"></svg>"""


class _SVGSensor(Sensor):
    def __sense__(self, *args, **kwargs) -> List[Event]:
        return [QueryXML.new("root", [])]


class _WebActuator(Actuator):
    @Actuator.action
    def attempt(self, event):
        return event


class WebSVGAvatar(Agent):

    # name of the remote socket route
    _SVG_SOCKET_ROUTE_NAME = "svg_socket_route"
    _SVG_SOCKET_TEMPLATE_NAME = "svg_socket.html.jinja"

    def __init__(self, *args, serve_kwargs=None, template_data=None, **kwargs):
        sensors = [_SVGSensor()]
        actuators = [_WebActuator()]
        super().__init__(sensors, actuators, *args, **kwargs)
        if serve_kwargs is None:
            serve_kwargs = dict(
                http_options=dict(port=DEFAULT_WEBSERVER_PORT, host="localhost")
            )
        serve.start(**serve_kwargs)
        self._socket_route = WebSVGAvatar._SVG_SOCKET_ROUTE_NAME
        if template_data is None:
            template_data = get_default_template_data()

        port = serve_kwargs["http_options"]["port"]
        host = serve_kwargs["http_options"]["host"]
        template_data["svg_socket"] = dict(route=self._socket_route)
        template_data["body"] += _JINJA2_ENV.get_template(
            WebSVGAvatar._SVG_SOCKET_TEMPLATE_NAME
        ).render(
            dict(
                address=f"{host}:{port}",
                route=self._socket_route,
                svg_code=DEFAULT_SVG_CODE,
            )
        )
        self._webserver = serve.run(WebServer.bind(template_data=template_data))
        self._webserver.open_socket.remote(self._socket_route)

    def __cycle__(self):
        # get latest sense data from sensors
        svg_data = self.sensors[0].get()[0].data["root"]
        # send sense data to the browser via the webserver
        self._webserver.update_socket.remote(self._socket_route, svg_data)
        actions = self._webserver.get_events.remote().result()
        # attempt each of the actions
        for action in actions:
            self.actuators[0].attempt(action)
