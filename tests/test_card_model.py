"""カードを別のモデルで検出する組み合わせ（DD-0019）。"""

from __future__ import annotations

from services import model_service
from services.inference_service import merge_card_detections


def _d(key, cx):
    return {"class_key": key, "cx": cx}


def test_cards_come_from_the_card_model_and_other_symbols_from_the_main_model():
    main = [_d("connector", 0.1), _d("other", 0.5), _d("contact_a", 0.2)]
    cards = [_d("other", 0.3), _d("other", 0.7), _d("connector", 0.9)]
    merged = merge_card_detections(main, cards)
    assert [(d["class_key"], d["cx"]) for d in merged] == [
        ("connector", 0.1), ("contact_a", 0.2), ("other", 0.3), ("other", 0.7)]


def test_card_model_is_found_beside_the_bundled_model_and_can_be_turned_off(tmp_path, monkeypatch):
    monkeypatch.delenv("SEQANNO_CARD_MODEL", raising=False)
    monkeypatch.setattr(model_service, "resource_base", lambda: tmp_path)
    monkeypatch.setattr(model_service, "is_frozen", lambda: True)
    assert model_service.card_model_path() is None
    card = tmp_path / "models" / model_service.CARD_MODEL_NAME
    card.parent.mkdir()
    card.write_bytes(b"PK")
    assert model_service.card_model_path() == card
    monkeypatch.setenv("SEQANNO_CARD_MODEL", "")
    assert model_service.card_model_path() is None


def test_status_reports_the_card_model(client, tmp_path, monkeypatch):
    card = tmp_path / "cards.pt"
    card.write_bytes(b"PK")
    monkeypatch.setenv("SEQANNO_CARD_MODEL", str(card))
    assert client.get("/api/ml/status").json()["card_model"] == {"name": "cards"}
    monkeypatch.setenv("SEQANNO_CARD_MODEL", "")
    assert client.get("/api/ml/status").json()["card_model"] is None

