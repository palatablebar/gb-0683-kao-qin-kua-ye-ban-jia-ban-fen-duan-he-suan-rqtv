from app.modules.m5_calculate.rules import apply
from app.modules.m5_calculate.overlap import adjudicate

def execute(state):
    return apply(adjudicate(state))
