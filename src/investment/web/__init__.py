"""Investment Web — v3 FastAPI application.

Provides DB-backed JSON APIs consumed by the multi-page frontend.
All data is read from portfolio.db (read-only for GET endpoints).

Routes:
  GET  /api/operating-state/today
  GET  /api/tasks?layer=&status=
  GET  /api/portfolio/health
  GET  /api/risk/summary
  GET  /api/goals/progress
  GET  /api/research/tasks
  GET  /api/data-quality/issues
  POST /api/tasks/{id}/complete
  POST /api/tasks/{id}/snooze
  POST /api/tasks/{id}/skip
"""
