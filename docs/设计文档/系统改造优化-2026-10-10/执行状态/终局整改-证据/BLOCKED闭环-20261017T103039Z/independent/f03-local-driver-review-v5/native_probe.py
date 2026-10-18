async def app(scope, receive, send):
    if scope["type"] != "lifespan":
        raise RuntimeError("No HTTP requests in independent control")
    while True:
        event = await receive()
        if event["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif event["type"] == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
            return
