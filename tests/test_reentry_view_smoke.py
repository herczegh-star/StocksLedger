"""Smoke testy pro reentry_view a app_flet Re-entry integrace — M-RE3."""
from __future__ import annotations

import inspect
from datetime import datetime
from decimal import Decimal


# ── Import smoke ──────────────────────────────────────────────────────────────

class TestReentryViewImport:

    def test_module_importable(self):
        from ui.modules.reentry_view import build_reentry_view
        assert callable(build_reentry_view)

    def test_correct_signature(self):
        from ui.modules.reentry_view import build_reentry_view
        params = inspect.signature(build_reentry_view).parameters
        assert "page"    in params
        assert "db_path" in params

    def test_colour_constants_defined(self):
        import ui.modules.reentry_view as rv
        for attr in ("BG_CARD", "BORDER", "T_PRI", "T_MUT", "BLUE", "BLUE_300", "GREEN", "RED"):
            assert hasattr(rv, attr), f"missing colour constant: {attr}"


# ── Data layer used by the view ───────────────────────────────────────────────

class TestReentryViewDataLayer:

    def test_empty_ledger_no_candidates(self, tmp_path):
        from core.services.ui_facade import create_db, get_reentry_snapshot
        db = str(tmp_path / "t.db")
        create_db(db)
        snap = get_reentry_snapshot(db)
        assert snap.candidates == []

    def test_sell_creates_candidate(self, tmp_path):
        from decimal import Decimal
        from core.services.ui_facade import (
            AddTradeRequestDTO, add_trade, create_db, get_reentry_snapshot,
        )
        db = str(tmp_path / "t.db")
        create_db(db)
        add_trade(AddTradeRequestDTO(
            type="BUY", timestamp=datetime(2024, 1, 1),
            asset="AAPL", amount=Decimal("5"), currency="EUR",
            price=Decimal("150"), quote_amount=Decimal("750"), venue="xtb",
        ), db)
        add_trade(AddTradeRequestDTO(
            type="SELL", timestamp=datetime(2024, 6, 1),
            asset="AAPL", amount=Decimal("5"), currency="EUR",
            price=Decimal("180"), quote_amount=Decimal("900"), venue="xtb",
        ), db)
        snap = get_reentry_snapshot(db)
        assert len(snap.candidates) == 1
        assert snap.candidates[0].sell_event.ticker == "AAPL"

    def test_candidate_unenriched_after_snapshot(self, tmp_path):
        from decimal import Decimal
        from core.services.ui_facade import (
            AddTradeRequestDTO, add_trade, create_db, get_reentry_snapshot,
        )
        db = str(tmp_path / "t.db")
        create_db(db)
        add_trade(AddTradeRequestDTO(
            type="BUY", timestamp=datetime(2024, 1, 1),
            asset="AAPL", amount=Decimal("5"), currency="EUR",
            price=Decimal("150"), quote_amount=Decimal("750"), venue="xtb",
        ), db)
        add_trade(AddTradeRequestDTO(
            type="SELL", timestamp=datetime(2024, 6, 1),
            asset="AAPL", amount=Decimal("5"), currency="EUR",
            price=Decimal("180"), quote_amount=Decimal("900"), venue="xtb",
        ), db)
        snap = get_reentry_snapshot(db)
        c = snap.candidates[0]
        # M-RE3: no price enrichment — all simulation fields must be None
        assert c.spot_eur       is None
        assert c.efficiency_pct is None
        assert c.is_alert       is False


# ── app_flet nav registration ─────────────────────────────────────────────────

class TestAppFletNavRegistration:

    def test_reentry_nav_item_present(self):
        """Verify Re-entry is registered in _NAV_ITEMS via source inspection."""
        import ast, pathlib
        src = pathlib.Path("ui/app_flet.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        # Search for "Re-entry" string literal anywhere in the AST
        found = any(
            isinstance(node, ast.Constant) and node.value == "Re-entry"
            for node in ast.walk(tree)
        )
        assert found, '"Re-entry" label not found in ui/app_flet.py _NAV_ITEMS'

    def test_ensure_reentry_view_function_present(self):
        """Verify the lazy init function exists in app_flet source."""
        import pathlib
        src = pathlib.Path("ui/app_flet.py").read_text(encoding="utf-8")
        assert "_ensure_reentry_view" in src

    def test_build_reentry_view_imported_in_app_flet(self):
        """Verify app_flet references build_reentry_view."""
        import pathlib
        src = pathlib.Path("ui/app_flet.py").read_text(encoding="utf-8")
        assert "build_reentry_view" in src
