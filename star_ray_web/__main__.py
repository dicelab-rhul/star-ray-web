from ray import serve
import time

from star_ray_web import AvatarWebServer

avatar = serve.run(AvatarWebServer.bind())

while True:
    # avatar.__cycle__.remote()
    time.sleep(2)
