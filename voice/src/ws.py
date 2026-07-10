"""WebSocket handler — real-time voice conversations."""
import asyncio
import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

from src.voice_common import (
    manager,
    get_stt_transport, get_llm_transport, get_tts_transport,
)

logger = logging.getLogger(__name__)


async def websocket_endpoint(websocket: WebSocket):
    client_id = f"client_{id(websocket)}"

    if not await manager.connect(websocket, client_id):
        return

    pipeline = websocket.app.state.pipeline if hasattr(websocket.app.state, 'pipeline') else None

    try:
        await websocket.send_json({
            "type": "connected",
            "client_id": client_id,
            "pipeline_ready": pipeline is not None and pipeline.initialized,
            "message": "WebSocket connected. Speak to begin."
        })

        while True:
            message = await websocket.receive()

            # #D39: the low-level receive() yields the ASGI disconnect message
            # ONCE; if we don't detect it and loop, the next receive() raises
            # RuntimeError('Cannot call "receive" once a disconnect message has
            # been received') which fell through to the generic handler and logged
            # an ERROR for every page close — flooding the voice-container logs.
            # Treat it as the normal end of the connection.
            if message.get("type") == "websocket.disconnect":
                logger.info(f"Client {client_id} disconnected")
                break

            if "bytes" in message:
                audio_data = message["bytes"]
                manager.process_audio_frame(client_id, audio_data)
                # Buffer accumulates until end_audio is received.
                # No mid-stream processing — all transcription happens on end_audio.

            elif "text" in message:
                data = json.loads(message["text"])
                if data.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
                elif data.get("type") == "start_recording":
                    mode = data.get("mode", "full")
                    manager.connection_modes[client_id] = mode
                    logger.info(f"Client {client_id} started recording (mode={mode})")
                elif data.get("type") == "end_audio":
                    mode = data.get("mode", "full")  # "full" or "transcribe"
                    buffer = manager.audio_buffers.get(client_id)
                    if buffer and len(buffer.frames) > 0:
                        combined_audio = buffer.get_buffered_audio()
                        buffer.clear()
                        await websocket.send_json({"type": "processing"})

                        if mode == "transcribe":
                            # Jules mode: STT only, skip LLM and TTS
                            stt = pipeline.stt if pipeline and hasattr(pipeline, 'stt') and pipeline.stt else None
                            if stt:
                                loop = asyncio.get_event_loop()
                                transcript = await loop.run_in_executor(None, stt.transcribe, combined_audio)
                                if transcript:
                                    await websocket.send_json({"type": "transcript", "text": transcript})
                                else:
                                    await websocket.send_json({"type": "silence"})
                            else:
                                await websocket.send_json({"type": "error", "message": "STT not ready"})
                            await websocket.send_json({"type": "done"})

                        elif pipeline and pipeline.initialized:
                            # Full pipeline: STT → LLM → TTS
                            result = await pipeline.process_audio(combined_audio)
                            if result.transcript:
                                await websocket.send_json({"type": "transcript", "text": result.transcript})
                            if result.response_text:
                                await websocket.send_json({"type": "response_text", "text": result.response_text})
                            logger.info(f"TTS result: audio={type(result.response_audio)}, len={len(result.response_audio) if result.response_audio else 0}")
                            if result.response_audio:
                                await websocket.send_json({"type": "speaking"})
                                await websocket.send_bytes(result.response_audio)
                            await websocket.send_json({"type": "done"})
                        else:
                            await websocket.send_json({"type": "error", "message": "Pipeline not ready"})
                    else:
                        await websocket.send_json({"type": "done"})

                    # Reset mode after end_audio
                    manager.connection_modes[client_id] = "full"

    except WebSocketDisconnect:
        logger.info(f"Client {client_id} disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        manager.disconnect(client_id)


async def process_audio_with_transcription(client_id: str, websocket: WebSocket):
    """Process audio frames with STT transcription."""
    stt = await get_stt_transport()

    if stt is None:
        await websocket.send_json({
            "type": "error",
            "message": "STT not available"
        })
        return

    buffer = manager.audio_buffers.get(client_id)
    if not buffer:
        return

    # Transcribe when we have enough audio (~2 seconds)
    if len(buffer.frames) >= 100:  # ~100 frames @ 20ms = 2 seconds
        audio_data = buffer.get_buffered_audio()

        # Run transcription in thread pool to not block
        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(
            None, stt.transcribe, audio_data
        )

        if text:
            await websocket.send_json({
                "type": "transcription",
                "text": text
            })
            # Clear buffer after successful transcription
            buffer.clear()

        await websocket.send_json({
            "type": "audio_received",
            "frames": len(buffer.frames),
            "bytes": len(audio_data),
            "transcribed": bool(text)
        })
    else:
        await websocket.send_json({
            "type": "audio_received",
            "frames": len(buffer.frames),
            "buffering": True
        })


async def process_audio_pipeline(client_id: str, websocket: WebSocket):
    """
    Full pipeline: Audio → STT → LLM → Response
    """
    stt = await get_stt_transport()
    llm = await get_llm_transport()

    if stt is None:
        await websocket.send_json({"type": "error", "message": "STT not available"})
        return

    buffer = manager.audio_buffers.get(client_id)
    if not buffer or len(buffer.frames) >= 100:
        audio_data = buffer.get_buffered_audio()

        # Run STT
        loop = asyncio.get_event_loop()
        transcript = await loop.run_in_executor(None, stt.transcribe, audio_data)

        if transcript:
            await websocket.send_json({
                "type": "transcription",
                "text": transcript
            })

            # Send to LLM if available
            if llm:
                await websocket.send_json({"type": "generating", "status": "started"})

                response_parts = []
                async for token in llm.generate(transcript):
                    response_parts.append(token)
                    await websocket.send_json({
                        "type": "token",
                        "token": token
                    })

                full_response = "".join(response_parts)
                await websocket.send_json({
                    "type": "response",
                    "text": full_response
                })

            buffer.clear()

        await websocket.send_json({
            "type": "pipeline_status",
            "frames": len(buffer.frames) if buffer else 0,
            "transcribed": bool(transcript)
        })


async def process_full_pipeline(client_id: str, websocket: WebSocket):
    """
    Complete pipeline: Audio → STT → LLM → TTS → Audio Response
    """
    stt = await get_stt_transport()
    llm = await get_llm_transport()
    tts = await get_tts_transport()

    buffer = manager.audio_buffers.get(client_id)
    if not buffer or len(buffer.frames) < 100:
        return

    audio_data = buffer.get_buffered_audio()

    # Step 1: STT
    if stt is None:
        await websocket.send_json({"type": "error", "message": "STT not available"})
        return

    loop = asyncio.get_event_loop()
    transcript = await loop.run_in_executor(None, stt.transcribe, audio_data)

    if not transcript:
        await websocket.send_json({"type": "status", "message": "No speech detected"})
        return

    await websocket.send_json({"type": "transcription", "text": transcript})
    buffer.clear()

    # Step 2: LLM
    if llm is None:
        await websocket.send_json({"type": "error", "message": "LLM not available"})
        return

    await websocket.send_json({"type": "generating", "status": "started"})

    response_text = ""
    async for token in llm.generate(transcript):
        response_text += token
        await websocket.send_json({"type": "token", "token": token})

    await websocket.send_json({"type": "response", "text": response_text})

    # Step 3: TTS
    if tts is None:
        await websocket.send_json({"type": "error", "message": "TTS not available"})
        return

    await websocket.send_json({"type": "synthesizing", "status": "started"})

    audio_response = await loop.run_in_executor(None, tts.synthesize, response_text)

    if audio_response:
        await websocket.send_json({
            "type": "audio_response",
            "audio": audio_response.hex(),  # Send as hex string for JSON
            "format": "wav",
            "size": len(audio_response)
        })
    else:
        await websocket.send_json({"type": "error", "message": "TTS synthesis failed"})
