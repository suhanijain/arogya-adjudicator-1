test:  ; PYTHONPATH=. pytest -q
serve: ; uvicorn server:app --reload
eval:  ; PYTHONPATH=. python eval_extractor.py
dash:  ; python ops/make_dashboard.py
