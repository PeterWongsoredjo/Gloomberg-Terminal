from fastapi import APIRouter, HTTPException, WebSocket, status

router = APIRouter()


@router.get("/tape")
async def get_tape_snapshot() -> None:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="tape snapshot not yet built")


@router.websocket("/tape/stream")
async def stream_tape(websocket: WebSocket) -> None:
    await websocket.accept()
    await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER)
