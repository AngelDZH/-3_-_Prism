from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from explain_unassigned import run_solver, run_solver_variants
from replan import run_replan
from baseline import run_baseline
from reassign import run_reassign

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_last_plan = {}

@app.get("/")
def root():
    return {"status": "ok", "message": "Bilain routing API работает"}

@app.post("/plan")
def get_plan():
    global _last_plan
    _last_plan = run_solver()
    return _last_plan

@app.post("/plan-variants")
def get_plan_variants():
    return run_solver_variants()

@app.post("/baseline")
def get_baseline():
    return run_baseline()

@app.post("/replan")
def get_replan(event_type: str = 'new_request', event_skill: str = 'emergency',
               event_window_start: str = '14:00', event_window_end: str = '16:00',
               event_base_request: str = 'REQ_050', cutoff_str: str = '14:00',
               cancel_request_id: str = None, unavailable_engineer_id: str = None):
    return run_replan(
        cutoff_str=cutoff_str, event_type=event_type, event_skill=event_skill,
        event_window_start=event_window_start, event_window_end=event_window_end,
        event_base_request=event_base_request,
        cancel_request_id=cancel_request_id,
        unavailable_engineer_id=unavailable_engineer_id,
    )

@app.post("/reassign")
def get_reassign(request_id: str, target_engineer_id: str):
    global _last_plan
    if not _last_plan:
        return {'error': 'Сначала постройте план через /plan'}
    result = run_reassign(_last_plan, request_id, target_engineer_id)
    if 'error' not in result:
        _last_plan = result
    return result
