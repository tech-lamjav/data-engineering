"""Roteamento por vendor do script consolidado extract_player_props.

Antes eram 3 scripts quase identicos (draftkings/caesars/betrivers); agora um so,
com main(vendor=...). Os wrappers Cloud Run por vendor chamam main(vendor='<x>').
"""
import sys
from pathlib import Path
from unittest.mock import patch

SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import extract_player_props as epp  # noqa: E402


@patch("extract_player_props.PlayerPropsExtractor")
def test_vendor_especifico_roteia_so_aquele(MockExtractor):
    inst = MockExtractor.return_value
    inst.extract_and_save.return_value = ["gs://b/x.json"]
    rc = epp.main(vendor="draftkings")
    assert rc == 0
    inst.extract_and_save.assert_called_once_with(vendors=["draftkings"])


@patch("extract_player_props.PlayerPropsExtractor")
def test_sem_vendor_roteia_todos_configurados(MockExtractor):
    inst = MockExtractor.return_value
    inst.vendors = ["draftkings", "caesars", "betrivers"]
    inst.extract_and_save.return_value = []
    rc = epp.main()
    assert rc == 0
    inst.extract_and_save.assert_called_once_with()
