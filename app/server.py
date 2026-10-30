"""FastAPI app: phone <-> Gemini Live bridge, frame ingest, live inventory, packet.

Kept from the reference app: the WebSocket bridge pattern (browser PCM16 audio +
JPEG frames in, model audio + transcripts out) and non-blocking tool calls.
New: high-res frame ingest over HTTP into the measuring pipeline, and the
pipeline's coaching/inventory messages fed back into the live session."""

import asyncio
import base64
import contextlib
import json
import logging
import os

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from google import genai
from google.genai import types

from . import config, live
from .explain import explain
from .sweep import Sweep

# Full detail goes to data/app.log (send this file when asking for help); the
# console only shows warnings so a beginner's window stays readable.
_console = logging.StreamHandler()
_console.setLevel(logging.WARNING)
_file = logging.FileHandler(config.DATA_DIR / "app.log", encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=[_console, _file])
log = logging.getLogger("server")
app = FastAPI(title="Library contents claim agent")
WEB = config.ROOT / "web"
SESSIONS: dict[str, dict] = {}   # sweep id -> {"sweep", "ui": set[WebSocket], "live": session|None}
KEY_STATUS = {"state": "checking", "detail": ""}

app.mount("/sweeps", StaticFiles(directory=config.SWEEPS_DIR), name="sweeps")


@app.on_event("startup")
async def start_key_check():
    # In the background, so the page loads at once and shows "checking".
    app.state.key_task = asyncio.create_task(check_key())


@app.on_event("shutdown")
async def close_vision_client():
    # Close while the loop still runs; otherwise the client's __del__ logs "Event loop is closed".
    from .vision import close_client
    with contextlib.suppress(Exception):
        await close_client()


async def check_key():
    if not config.has_key():
        KEY_STATUS.update(state="missing", detail="No Google API key in the .env file.")
        return

    def probe():
        from google import genai
        client = genai.Client(api_key=config.GOOGLE_API_KEY)  # keep a reference while the pager is read
        next(iter(client.models.list()))

    try:
        await asyncio.wait_for(asyncio.to_thread(probe), 20)
        KEY_STATUS.update(state="ok", detail="")
    except Exception as exc:
        KEY_STATUS.update(state="invalid" if "API_KEY_INVALID" in str(exc) else "unknown", detail=explain(exc))
    log.info("Key status: %s %s; models live=%s vision=%s pricing=%s", KEY_STATUS["state"], KEY_STATUS["detail"],
             config.LIVE_MODEL, config.VISION_MODEL, config.PRICING_MODEL)


def _session(sweep_id: str) -> dict:
    s = SESSIONS.get(sweep_id)
    if not s:
        raise HTTPException(404, "unknown sweep")
    return s


async def _broadcast(sweep_id: str, event: dict):
    s = SESSIONS.get(sweep_id)
    if not s:
        return
    for ws in list(s["ui"]):
        try:
            await ws.send_json(event)
        except Exception:
            s["ui"].discard(ws)
    # Coaching goes straight to the voice agent; inventory is throttled context.
    if s.get("live") and event["type"] == "coach":
        await _tell_agent(s, "[CAPTURE MONITOR] " + event["text"])
    if s.get("live") and event["type"] == "inventory":
        c = event["counts"]
        loop = asyncio.get_running_loop()
        if loop.time() - s.get("last_inv", 0) > 20:
            s["last_inv"] = loop.time()
            recent = [b["title"] for b in event["books"] if b["title"]][-3:]
            await _tell_agent(s, f'[INVENTORY] {c["books"]} books logged ({c["identified"]} identified), {c["items"]} other items, '
                                 f'{c["walls"]} walls measured. Recently identified: {", ".join(recent) or "none yet"}. '
                                 "Do not reply to this message unless it is useful to mention progress.")


async def _tell_agent(s: dict, text: str):
    with contextlib.suppress(Exception):
        await s["live"].send_realtime_input(text=text)


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/app.js")
def js():
    return FileResponse(WEB / "app.js", media_type="text/javascript")


@app.get("/bargein.js")
def bargein_js():
    return FileResponse(WEB / "bargein.js", media_type="text/javascript")


@app.get("/ui.js")
def ui_js():
    return FileResponse(WEB / "ui.js", media_type="text/javascript")


@app.get("/avatar.js")
def avatar_js():
    return FileResponse(WEB / "avatar.js", media_type="text/javascript")


@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)


@app.get("/api/health")
def health():
    return {"ok": True, "key": KEY_STATUS, "models_tested": bool(config._tested),
            "live_model": config.LIVE_MODEL, "vision_model": config.VISION_MODEL, "pricing_model": config.PRICING_MODEL,
            "country": config.COUNTRY, "currency": config.CURRENCY, "phone_url": os.getenv("PHONE_URL", "")}


@app.get("/api/phone-qr.svg")
def phone_qr():
    """QR code of the phone address (set by app.launch), shown on the computer's page."""
    url = os.getenv("PHONE_URL", "")
    if not url:
        raise HTTPException(404, "phone mode is not running")
    import qrcode
    import qrcode.image.svg
    svg = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, border=2).to_string()
    return Response(content=svg, media_type="image/svg+xml")


@app.post("/api/sweeps")
async def create_sweep(request: Request):
    body = await request.json() if request.headers.get("content-length") not in (None, "0") else {}
    device = body.get("device") or request.headers.get("user-agent", "")[:120]
    holder: dict = {}

    async def notify(event):
        await _broadcast(holder["id"], event)

    sweep = Sweep(notify, device=device)
    holder["id"] = sweep.id
    SESSIONS[sweep.id] = {"sweep": sweep, "ui": set(), "live": None}
    return {"id": sweep.id, "country": sweep.country, "currency": sweep.currency}


@app.post("/api/sweeps/{sweep_id}/frames")
async def upload_frame(sweep_id: str, request: Request):
    data = await request.body()
    if not data.startswith(b"\xff\xd8\xff") or len(data) > 4_000_000:
        raise HTTPException(400, "expected a JPEG under 4 MB")
    return await _session(sweep_id)["sweep"].add_frame(data)


@app.post("/api/sweeps/{sweep_id}/segment")
async def segment(sweep_id: str, request: Request):
    """Manual fallback for the agent's start_* tools (UI buttons)."""
    body = await request.json()
    s = _session(sweep_id)
    seg = s["sweep"].start_segment(body["kind"], str(body.get("label", "")))
    if s.get("live"):
        await _tell_agent(s, f'[CAPTURE MONITOR] The policyholder pressed the button for {seg["kind"]} {seg["label"]}. Give them the instruction for it.')
    return seg


@app.post("/api/sweeps/{sweep_id}/finish")
async def finish(sweep_id: str):
    return await _finish(_session(sweep_id))


async def _finish(s: dict) -> dict:
    sweep = s["sweep"]
    result = await sweep.finish()
    summary = live.packet_summary(result)
    await _broadcast(sweep.id, {"type": "packet", "url": f"/sweeps/{sweep.id}/report.html",
                                "json": f"/sweeps/{sweep.id}/claim_packet.json", "summary": summary,
                                "time_to_packet_s": result["metrics"]["time_to_packet_s"]})
    if s.get("live"):
        await _tell_agent(s, summary)
    return {"summary": summary, "report": f"/sweeps/{sweep.id}/report.html"}


async def _add_correction(s: dict, body: dict) -> dict:
    sweep = s["sweep"]
    result = sweep.add_correction(str(body.get("target", "")), str(body.get("title", "") or ""), str(body.get("author", "") or ""),
                                  body.get("replacement_cost"), body.get("used_value"), str(body.get("comment", "") or ""))
    if result.get("ok"):
        await _broadcast(sweep.id, {"type": "corrections", "corrections": sweep.corrections})
    return result


async def _build_customer(s: dict, tell_agent: bool) -> dict:
    sweep = s["sweep"]
    try:
        p = sweep.build_customer_packet()
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    summary = live.customer_summary(p)
    await _broadcast(sweep.id, {"type": "customer_packet", "url": f"/sweeps/{sweep.id}/customer_advised_report.html",
                                "json": f"/sweeps/{sweep.id}/customer_advised_claim_packet.json",
                                "summary": summary, "version": p["customer_version"]})
    if tell_agent and s.get("live"):
        await _tell_agent(s, summary)
    return {"ok": True, "summary": summary}


@app.post("/api/sweeps/{sweep_id}/corrections")
async def add_correction(sweep_id: str, request: Request):
    """Typed corrections from the page (same as the agent's add_customer_correction tool)."""
    return await _add_correction(_session(sweep_id), await request.json())


@app.post("/api/sweeps/{sweep_id}/customer-packet")
async def customer_packet(sweep_id: str):
    return await _build_customer(_session(sweep_id), tell_agent=True)


async def _run_tool(s: dict, fc) -> dict:
    sweep, args = s["sweep"], dict(fc.args or {})
    name = fc.name
    if name == "set_locale" and not s.get("user_spoke"):
        # Locale is a fact about the policyholder: only they can state it.
        return {"ok": False, "error": "The policyholder has not answered yet. Ask them to confirm the country and currency first."}
    if name == "set_locale":
        sweep.set_locale(args.get("country_code", config.COUNTRY), args.get("currency_code", config.CURRENCY))
        return {"ok": True, "country": sweep.country, "currency": sweep.currency}
    if name == "start_wall":
        return {"ok": True, **sweep.start_segment("wall", str(args.get("label", "")))}
    if name == "start_shelf":
        return {"ok": True, **sweep.start_segment("shelf", str(args.get("label", "")))}
    if name == "start_room_view":
        return {"ok": True, **sweep.start_segment("room", "room")}
    if name == "start_front_view":
        return {"ok": True, **sweep.start_segment("front", "")}
    if name == "record_fact":
        return {"ok": True, "recorded": sweep.add_fact(args.get("kind", ""), str(args.get("target", "")), str(args.get("note", "")))}
    if name == "get_progress":
        books, warnings = sweep.books()
        return {"books": len(books), "identified": sum(1 for b in books if b["ident"] and b["ident"]["status"] == "identified"),
                "items": len(sweep.items), "walls_measured": sorted(sweep.walls), **sweep.shelf_progress(),
                "warnings": warnings, "frames_waiting_for_analysis": sweep.queue.qsize()}
    if name == "add_customer_correction":
        return await _add_correction(s, args)
    if name == "build_customer_packet":
        return await _build_customer(s, tell_agent=False)
    if name == "end_sweep":
        missed = sweep.shelf_progress()["shelves_possibly_missed"]
        if missed and not s.get("missed_warned"):
            # Catch missed shelves while the policyholder is still in the room; a second call ends anyway.
            s["missed_warned"] = True
            return {"ok": False, "warning": f"The wall shots show about {missed} more shelf(s) than were swept. "
                    "Ask whether to cover them now; call end_sweep again to finish anyway."}
        if not sweep.finished:
            asyncio.create_task(_finish(s))
        return {"ok": True, "message": "Building the packet now. Tell them it takes a minute or two; a [PACKET READY] message will follow."}
    return {"error": "unknown tool"}


@app.websocket("/ws/{sweep_id}")
async def live_ws(websocket: WebSocket, sweep_id: str):
    s = SESSIONS.get(sweep_id)
    if not s:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    s["ui"].add(websocket)
    send_lock = asyncio.Lock()

    async def send(payload):
        async with send_lock:
            await websocket.send_json(payload)

    try:
        if not config.has_key():
            await send({"type": "error", "message": "No Google API key yet. Close the app, run the start script again and paste your key when asked."})
            return
        client = genai.Client(api_key=config.GOOGLE_API_KEY)
        try:
            connection = client.aio.live.connect(model=config.LIVE_MODEL, config=live.connect_config())
            session = await connection.__aenter__()
        except Exception as exc:
            log.exception("live connect failed (model %s)", config.LIVE_MODEL)
            await send({"type": "error", "message": "The voice agent could not start. " + explain(exc)})
            return
        try:
            s["live"] = session
            await send({"type": "ready", "model": config.LIVE_MODEL})
            await session.send_realtime_input(text="[CAPTURE MONITOR] The call just connected. Greet the policyholder and start.")

            async def upstream():
                while True:
                    msg = json.loads(await websocket.receive_text())
                    kind = msg.get("type")
                    if kind == "audio":
                        await session.send_realtime_input(audio=types.Blob(data=base64.b64decode(msg["data"]), mime_type="audio/pcm;rate=16000"))
                    elif kind == "video":
                        await session.send_realtime_input(video=types.Blob(data=base64.b64decode(msg["data"]), mime_type="image/jpeg"))
                    elif kind == "text":
                        s["user_spoke"] = True
                        await session.send_realtime_input(text=msg["text"])
                    elif kind == "barge_in":
                        # The phone paused the agent locally; the model's own voice detection confirms it.
                        s["sweep"].barge_ins["local"] += 1
                        s["sweep"]._log_event({"event": "barge_in", "by": "phone"})
                    elif kind == "close":
                        return

            async def tool_task(fc):
                try:
                    result = await _run_tool(s, fc)
                except Exception as exc:
                    log.exception("tool %s failed", fc.name)
                    result = {"error": str(exc)[:200]}
                await send({"type": "tool", "name": fc.name, "args": dict(fc.args or {}), "result": result})
                await session.send_tool_response(function_responses=[types.FunctionResponse(id=fc.id, name=fc.name, response=result)])

            async def downstream():
                while True:
                    async for resp in session.receive():
                        if resp.tool_call and resp.tool_call.function_calls:
                            for fc in resp.tool_call.function_calls:
                                asyncio.create_task(tool_task(fc))
                        c = resp.server_content
                        if not c:
                            continue
                        if c.interrupted:
                            s["sweep"].barge_ins["confirmed"] += 1
                            s["sweep"]._log_event({"event": "barge_in", "by": "model"})
                            await send({"type": "interrupted"})
                        if c.input_transcription and c.input_transcription.text:
                            s["user_spoke"] = True
                            await send({"type": "transcript", "speaker": "you", "text": c.input_transcription.text})
                        if c.output_transcription and c.output_transcription.text:
                            await send({"type": "transcript", "speaker": "agent", "text": c.output_transcription.text})
                        if c.model_turn and not c.interrupted:  # never play audio of a reply that was cut off
                            for part in c.model_turn.parts or []:
                                if part.inline_data and (part.inline_data.mime_type or "").startswith("audio"):
                                    await send({"type": "audio", "data": base64.b64encode(part.inline_data.data).decode()})
                        if c.turn_complete:
                            await send({"type": "turn_complete"})

            tasks = [asyncio.create_task(upstream()), asyncio.create_task(downstream())]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
            for t in done:
                t.result()
        finally:
            s["live"] = None
            with contextlib.suppress(Exception):
                await connection.__aexit__(None, None, None)
            with contextlib.suppress(Exception):
                await client.aio.aclose()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.exception("live session failed")
        with contextlib.suppress(Exception):
            await send({"type": "error", "message": "The voice call stopped. " + explain(exc) + " Press Start again to reconnect."})
    finally:
        s["live"] = None
        s["ui"].discard(websocket)
        with contextlib.suppress(Exception):
            await websocket.close()


@app.get("/api/sweeps/{sweep_id}/packet")
def get_packet(sweep_id: str):
    path = config.SWEEPS_DIR / sweep_id / "claim_packet.json"
    if not path.exists():
        raise HTTPException(404, "packet not built yet")
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))
