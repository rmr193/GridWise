from __future__ import annotations

from pathlib import Path
from fastapi import Depends, FastAPI, HTTPException
from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_FILE)

from app.guardrails import InterpretationError
from app.llm import LLMInterpreter
from app.models import DirectiveInterpretation, OptimizeRequest, OptimizeResponse
from app.optimizer import OptimizationError, optimize


app = FastAPI(title="GridWise Energy Scheduling API", version="1.0.0")


def get_interpreter() -> LLMInterpreter:
    return LLMInterpreter()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/optimize-energy", response_model=OptimizeResponse)
async def optimize_energy(
    request: OptimizeRequest,
    interpreter: LLMInterpreter = Depends(get_interpreter),
) -> OptimizeResponse:
    try:
        directives: list[DirectiveInterpretation] = await interpreter.interpret(request)
        return optimize(request, directives)
    except InterpretationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except OptimizationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
