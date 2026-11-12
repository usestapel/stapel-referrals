"""Localized error catalogs (``translations/errors.<lang>.json``) + reference.

The convention of stapel-profiles (and stapel-feed): the en canon is the registry
(``errors.py`` → ``docs/errors.json``); every target language ships a flat
``translations/errors.<lang>.json`` with the shared ``translations/.state.json``
provenance sidecar, and ``docs/errors.<lang>.md`` is the per-language error
reference. :func:`check_translation_catalogs` gates coverage, staleness,
placeholders and byte stability. Core-owned keys are core's to translate and
never copied here.

The strings are kept in :data:`_MACHINE` (``origin: llm``) so the catalog
regenerates deterministically without a live model; they were seeded from
the hand-written ru/es error tables of the react pair
(``@stapel/referrals-react`` ``src/i18n/{ru,es}.ts``), which can now read
them from the brick instead.
Adding a language: append it to :data:`LANGUAGES`, add its table, regenerate.

Regenerate after adding/changing an error key or a translation:

    STAPEL_REGEN_ERROR_I18N=1 python -m pytest tests/test_error_i18n.py::test_regen

then commit ``translations/`` + ``docs/errors.<lang>.md``. Without the env
var the same module is the CI gate.
"""
import io
import os
import tomllib
from pathlib import Path

import pytest
from django.core.management import call_command
from stapel_core.i18n import (
    check_translation_catalogs,
    source_texts,
    summarize,
    translate_catalog,
)
from stapel_core.i18n.catalogs import load_catalog_file

REPO = Path(__file__).resolve().parent.parent
TRANSLATIONS = REPO / "translations"
DOCS = REPO / "docs"
THIS_PACKAGE = "stapel_referrals"
LANGUAGES = ["en", "ru", "es"]
TARGET_LANGUAGES = [lang for lang in LANGUAGES if lang != "en"]

_MACHINE_RU = {
    "error.400.code_unknown": "Такого кода приглашения нет",
    "error.400.reward_params_invalid": "Параметры награды неверны: {problem}",
    "error.400.reward_rule_invalid": "Правило награды неверно: {problem}",
    "error.400.reward_strategy_unknown": "Неизвестный способ награды {strategy}",
    "error.400.self_referral": "Нельзя воспользоваться собственным кодом приглашения",
    "error.400.vanity_code_invalid": "Код — от 4 до 32 букв, цифр, «-» или «_»",
    "error.403.claim_requires_account": "Войдите, чтобы воспользоваться кодом приглашения",
    "error.403.vanity_code_not_allowed": "Выбрать свой код нельзя",
    "error.404.referral_code_not_found": "Код приглашения не найден",
    "error.404.reward_not_found": "Награда не найдена",
    "error.404.reward_rule_not_found": "Правило награды не найдено",
    "error.409.already_attributed": "Вы уже воспользовались кодом приглашения",
    "error.409.attribution_window_closed": "Срок этого приглашения истёк",
    "error.409.claim_refused": "Этот код приглашения нельзя использовать с этой учётной записи",
    "error.409.claim_window_closed": "Коды приглашения — только для новых учётных записей",
    "error.409.code_exhausted": "Этот код приглашения исчерпан",
    "error.409.code_expired": "Этот код приглашения сейчас не действует",
    "error.409.code_taken": "Этот код занят — выберите другой",
    "error.409.reward_transition_invalid": "Награду нельзя перевести из {from} в {to}",
    "error.409.vanity_change_limit": "Код больше менять нельзя (не более {max} раз)",
    "error.503.reward_provider_unavailable": "Награду не удалось выдать сейчас; попытка повторится",
}

_MACHINE_ES = {
    "error.400.code_unknown": "Este código de invitación no es válido",
    "error.400.reward_params_invalid": "Los parámetros de la recompensa no son válidos: {problem}",
    "error.400.reward_rule_invalid": "La regla de recompensa no es válida: {problem}",
    "error.400.reward_strategy_unknown": "Estrategia de recompensa desconocida {strategy}",
    "error.400.self_referral": "No puedes usar tu propio código de invitación",
    "error.400.vanity_code_invalid": "Un código tiene de 4 a 32 letras, dígitos, '-' o '_'",
    "error.403.claim_requires_account": "Inicia sesión para usar un código de invitación",
    "error.403.vanity_code_not_allowed": "No se puede elegir un código propio",
    "error.404.referral_code_not_found": "Código de invitación no encontrado",
    "error.404.reward_not_found": "Recompensa no encontrada",
    "error.404.reward_rule_not_found": "Regla de recompensa no encontrada",
    "error.409.already_attributed": "Ya has usado un código de invitación",
    "error.409.attribution_window_closed": "Esta invitación ha caducado",
    "error.409.claim_refused": "Este código de invitación no se puede usar desde esta cuenta",
    "error.409.claim_window_closed": "Los códigos de invitación son para cuentas nuevas",
    "error.409.code_exhausted": "Este código de invitación se ha agotado",
    "error.409.code_expired": "Este código de invitación no está activo ahora",
    "error.409.code_taken": "Este código ya está en uso; elige otro",
    "error.409.reward_transition_invalid": "La recompensa no puede pasar de {from} a {to}",
    "error.409.vanity_change_limit": "Ya no puedes cambiar tu código (como máximo {max} cambios)",
    "error.503.reward_provider_unavailable": "No se pudo emitir la recompensa ahora; se volverá a intentar",
}

_MACHINE = {"ru": _MACHINE_RU, "es": _MACHINE_ES}


class _DictTranslator:
    """Offline translator seam — returns fixed machine translations by key."""

    def __init__(self, table):
        self._table = table

    def translate(self, entries, source_language, target_language):
        return {k: self._table[k] for k in entries if k in self._table}


def _regen(lang: str):
    return translate_catalog(
        "errors", lang, TRANSLATIONS,
        source_texts=source_texts("errors"),
        llm=True,
        translator=_DictTranslator(_MACHINE[lang]),
    )


def _owned() -> dict[str, str]:
    from stapel_core.i18n import owned_keys, source_owners

    return owned_keys(source_texts("errors"), source_owners("errors"), THIS_PACKAGE)


def test_regen():
    """Regenerate (env-gated) or assert every catalog is a no-op regen (drift)."""
    if os.environ.get("STAPEL_REGEN_ERROR_I18N"):
        for lang in TARGET_LANGUAGES:
            result = _regen(lang)
            assert not result.missing, f"{lang}: still missing: {result.missing}"
        for lang in LANGUAGES:
            call_command("generate_error_docs", "--lang", lang,
                         "--out", str(DOCS), "--translations", str(TRANSLATIONS),
                         stdout=io.StringIO())
        return

    for lang in TARGET_LANGUAGES:
        path = TRANSLATIONS / f"errors.{lang}.json"
        before = path.read_bytes()
        _regen(lang)
        assert path.read_bytes() == before, (
            f"errors.{lang}.json drifted — run "
            f"STAPEL_REGEN_ERROR_I18N=1 pytest tests/test_error_i18n.py::test_regen"
        )


def test_catalog_gate_green():
    """Missing / stale / params / byte-stability / foreign: all zero."""
    issues = check_translation_catalogs(
        "errors", TRANSLATIONS,
        source_texts=source_texts("errors"),
        languages=LANGUAGES,
        owner=THIS_PACKAGE,
    )
    errors, _warnings = summarize(issues)
    blocking = [i for i in issues if i.level == "error"]
    assert not blocking, "\n".join(f"[{i.code}/{i.language}] {i.message}" for i in blocking)
    assert errors == 0


def test_every_language_covers_every_code_in_errors_py():
    from stapel_referrals.errors import STAPEL_REFERRALS_ERRORS

    owned = _owned()
    assert set(owned) == set(STAPEL_REFERRALS_ERRORS), (
        "the loader does not attribute this module's registry to it"
    )
    for lang in TARGET_LANGUAGES:
        catalog = load_catalog_file(TRANSLATIONS / f"errors.{lang}.json")
        missing = sorted(set(STAPEL_REFERRALS_ERRORS) - set(catalog))
        assert not missing, f"{lang} catalog misses {missing}"
        empty = sorted(k for k, v in catalog.items() if not str(v).strip())
        assert not empty, f"{lang}: empty translation(s): {empty}"


@pytest.mark.parametrize("lang", TARGET_LANGUAGES)
def test_the_catalog_carries_nothing_but_owned_keys(lang):
    stray = sorted(set(load_catalog_file(TRANSLATIONS / f"errors.{lang}.json")) - set(_owned()))
    assert not stray, f"{lang}: not this module's keys: {stray}"


@pytest.mark.parametrize("lang", TARGET_LANGUAGES)
def test_translations_preserve_placeholders(lang):
    from stapel_core.i18n.domains import params_of

    canon = _owned()
    for key, text in load_catalog_file(TRANSLATIONS / f"errors.{lang}.json").items():
        assert set(params_of(text)) == set(params_of(canon[key])), f"{lang}: {key}"


def test_the_shipped_language_set_is_exactly_the_gated_one():
    shipped = {p.name for p in TRANSLATIONS.glob("errors.*.json")}
    assert shipped == {f"errors.{lang}.json" for lang in TARGET_LANGUAGES}


def test_error_reference_matches_a_fresh_regeneration(tmp_path):
    for lang in LANGUAGES:
        call_command("generate_error_docs", "--lang", lang, "--out", str(tmp_path),
                     "--translations", str(TRANSLATIONS), stdout=io.StringIO())
        assert (tmp_path / f"errors.{lang}.md").read_bytes() == \
            (DOCS / f"errors.{lang}.md").read_bytes(), (
                f"docs/errors.{lang}.md is stale — run "
                f"STAPEL_REGEN_ERROR_I18N=1 pytest tests/test_error_i18n.py::test_regen"
            )


def test_error_reference_has_no_en_fallback_rows():
    for lang in TARGET_LANGUAGES:
        assert "_(en)_" not in (DOCS / f"errors.{lang}.md").read_text(), (
            f"{lang} error reference has en-fallback rows — the catalog is incomplete"
        )


def test_the_wheel_ships_the_catalogue():
    """package-data must carry ``translations/*.json``, or no host reads it."""
    with open(REPO / "pyproject.toml", "rb") as handle:
        patterns = tomllib.load(handle)["tool"]["setuptools"]["package-data"]
    assert "translations/*.json" in patterns[THIS_PACKAGE]
