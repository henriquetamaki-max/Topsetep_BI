"""Test package. Silencia warnings de Streamlit em bare mode (sem runtime).

A env var `STREAMLIT_LOG_LEVEL=ERROR` precisa ser setada ANTES de qualquer
import de streamlit. tests/__init__.py e' executado pelo unittest discover
antes de carregar cada test module — entao funciona como bootstrap global.
"""
import logging
import os

os.environ.setdefault("STREAMLIT_LOG_LEVEL", "ERROR")

for _name in (
    "streamlit.runtime.scriptrunner_utils.script_run_context",
    "streamlit.runtime.caching.cache_data_api",
    "streamlit.runtime.state.session_state_proxy",
    "streamlit",
):
    logging.getLogger(_name).setLevel(logging.ERROR)
