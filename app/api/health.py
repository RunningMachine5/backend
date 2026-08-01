from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    """컨테이너와 FastAPI 프로세스의 생존 상태를 반환한다."""
    return {"status": "ok"}

