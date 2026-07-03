from fastapi import APIRouter, HTTPException, status

router = APIRouter()


@router.get("/sentiment/matrix")
async def get_sentiment_matrix() -> None:
    """CT-011<SentimentMatrix> — wiring lands with the serving stage (SV-04)."""
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="sentiment matrix not yet built")
