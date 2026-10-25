"""Generates the Grafana dashboard JSON. Run: python ops/make_dashboard.py"""
import json
from pathlib import Path

PANELS = [
    ("Claims by outcome", "sum by (outcome) (arogya_claims_total)", "barchart"),
    ("Approvals waiting", "arogya_approvals_pending", "stat"),
    ("Rupees settled", "arogya_settled_rupees_total", "stat"),
    ("Rupees deducted by reason", "sum by (reason) (arogya_deducted_rupees_total)", "barchart"),
    ("Fraud flags by type", "sum by (type) (arogya_fraud_flags_total)", "barchart"),
    ("Node time p95 (s)", "histogram_quantile(0.95, sum by (le, node) (rate(arogya_node_seconds_bucket[5m])))", "timeseries"),
    ("Model calls by mode", "sum by (mode) (arogya_llm_calls_total)", "piechart"),
    ("Adjudicator decisions", "sum by (action, level) (arogya_decisions_total)", "barchart"),
    ("Critic send-backs", "arogya_critic_revisions_total", "stat"),
    ("Lessons by status", "arogya_lessons", "barchart"),
]
panels = [{"id": i + 1, "title": t, "type": ty, "datasource": "Prometheus", "gridPos": {"x": (i % 3) * 8, "y": (i // 3) * 8, "w": 8, "h": 8},
           "targets": [{"expr": e, "legendFormat": "{{outcome}}{{reason}}{{type}}{{node}}{{mode}}{{action}}{{status}}", "refId": "A"}]} for i, (t, e, ty) in enumerate(PANELS)]
Path(__file__).parent.joinpath("grafana/dashboards/arogya.json").write_text(json.dumps({"title": "Arogya claims", "uid": "arogya", "schemaVersion": 39, "refresh": "10s", "panels": panels}, indent=1))
