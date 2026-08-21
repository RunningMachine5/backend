"""운영 화면에서 실행하는 시연 거래 주입 API."""

from collections.abc import Callable
from functools import partial
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from app.api.mlops import require_mlops_admin
from app.dto.demo_transaction import DemoTransactionInjectionStatus
from app.services.demo_transaction_injection import (
    demo_transaction_injection_manager,
    run_demo_transaction_injection,
)
from app.services.ml_serving.client import MLServingClientDep

router = APIRouter(
    prefix="/demo-transactions",
    tags=["demo-transactions-admin"],
    dependencies=[Depends(require_mlops_admin)],
)


def get_demo_transaction_runner(
    ml_serving_client: MLServingClientDep,
) -> Callable[[], None]:
    return partial(run_demo_transaction_injection, ml_serving_client)


DemoTransactionRunnerDep = Annotated[
    Callable[[], None],
    Depends(get_demo_transaction_runner),
]


@router.get("/injection", response_model=DemoTransactionInjectionStatus)
def get_demo_transaction_injection_status() -> DemoTransactionInjectionStatus:
    return demo_transaction_injection_manager.snapshot()


@router.post(
    "/injection",
    response_model=DemoTransactionInjectionStatus,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_demo_transaction_injection(
    background_tasks: BackgroundTasks,
    runner: DemoTransactionRunnerDep,
) -> DemoTransactionInjectionStatus:
    if not demo_transaction_injection_manager.start():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="시연 거래 100건을 이미 주입하고 있습니다.",
        )
    background_tasks.add_task(runner)
    return demo_transaction_injection_manager.snapshot()


__all__ = ["get_demo_transaction_runner", "router"]
