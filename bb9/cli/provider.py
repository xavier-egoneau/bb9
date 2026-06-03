"""Interactive provider configuration commands."""

from __future__ import annotations

from typing import Any

from .auth_flow import ChatGPTOAuthFlow, OAuthError
from .provider_config import (
    AUTH_API,
    AUTH_WEB,
    PROVIDER_REGISTRY,
    ModelFetchError,
    ProviderEntry,
    ProviderStore,
    default_web_token_path,
    fetch_models,
    normalize_api_key_ref_input,
    normalize_base_url,
    public_secret_label,
    write_web_token,
)


def cmd_model(cli: Any, value: str) -> bool:
    if value.strip() == "show":
        print_details(cli)
        return True
    run_wizard(cli)
    return True


def print_details(cli: Any) -> None:
    if cli.state.active_provider is None:
        print(cli.state.model or "-")
        return
    entry = cli.state.active_provider
    print(f"provider... {entry.name} ({entry.provider})")
    print(f"auth....... {entry.auth_type}")
    print(f"base....... {normalize_base_url(entry.provider, entry.base_url) or '-'}")
    print(f"secret..... {public_secret_label(entry.api_key_ref) or '-'}")
    print(f"model...... {entry.model or '-'}")
    metadata = cli.active_model_metadata()
    print(f"context.... {metadata.context_window_tokens} ({metadata.source})")
    if metadata.soft_input_limit_tokens:
        print(f"soft....... {metadata.soft_input_limit_tokens}")


def run_wizard(cli: Any) -> None:
    store = ProviderStore(cli.state.provider_config_path)
    config = store.load()
    entries = list(config.entries)

    print()
    print("Choix du provider et du modele")
    if entries:
        active = config.active_entry()
        if active is not None:
            print(f"Actif: {active.name} / {active.model or '-'}")
        print()
        for index, entry in enumerate(entries, 1):
            marker = "*" if active and entry.id == active.id else " "
            print(f"{index}. {marker} {entry.name} ({entry.provider}, {entry.auth_type}) / {entry.model or '-'}")
        print(f"{len(entries) + 1}. + ajouter un provider")
        raw = input(f"Choix [1-{len(entries) + 1}] : ").strip()
        if not raw and active is not None:
            configure_existing(cli, store, active)
            return
        try:
            choice = int(raw)
        except ValueError:
            print("Choix annule.")
            return
        if 1 <= choice <= len(entries):
            configure_existing(cli, store, entries[choice - 1])
            return
        if choice != len(entries) + 1:
            print("Choix annule.")
            return

    add_provider(cli, store)


def configure_existing(cli: Any, store: ProviderStore, entry: ProviderEntry) -> None:
    models = fetch_models_for_wizard(entry)
    model = choose_model(models, current=entry.model)
    if not model:
        print("Choix annule.")
        return
    updated = ProviderEntry(
        id=entry.id,
        name=entry.name,
        provider=entry.provider,
        auth_type=entry.auth_type,
        base_url=entry.base_url,
        api_key_ref=entry.api_key_ref,
        model=model,
        added_at=entry.added_at,
        metadata=entry.metadata,
    )
    store.upsert(updated, active=True)
    cli.set_active_provider(updated)
    print(f"Modele actif: {updated.name} / {updated.model}")


def add_provider(cli: Any, store: ProviderStore) -> None:
    definitions = list(PROVIDER_REGISTRY.values())
    print()
    print("Providers")
    for index, definition in enumerate(definitions, 1):
        print(f"{index}. {definition.label} ({definition.kind})")
    raw = input(f"Provider [1-{len(definitions)}] : ").strip()
    try:
        provider_choice = int(raw)
    except ValueError:
        print("Ajout annule.")
        return
    if not 1 <= provider_choice <= len(definitions):
        print("Ajout annule.")
        return

    definition = definitions[provider_choice - 1]
    auth_types = list(definition.supported_auth_types)
    print()
    print("Authentification")
    for index, auth_type in enumerate(auth_types, 1):
        label = "API key via env/file" if auth_type == AUTH_API else "web/auth locale"
        print(f"{index}. {auth_type} - {label}")
    raw = input(f"Auth [1-{len(auth_types)}] : ").strip()
    try:
        auth_choice = int(raw)
    except ValueError:
        print("Ajout annule.")
        return
    if not 1 <= auth_choice <= len(auth_types):
        print("Ajout annule.")
        return

    auth_type = auth_types[auth_choice - 1]
    provider_id = ProviderEntry.new_id()
    base_url = definition.default_base_url
    api_key_ref = ""
    metadata = {}

    if auth_type == AUTH_API:
        raw_base_url = input(f"Base URL [{definition.default_base_url}] : ").strip() or definition.default_base_url
        base_url = normalize_base_url(definition.kind, raw_base_url)
        if base_url != raw_base_url.rstrip("/"):
            print(f"Base URL normalisee: {base_url}")
        if definition.default_api_key_env:
            default_ref = f"env:{definition.default_api_key_env}"
            raw_ref = input(f"Secret ref ou cle brute [{default_ref}] : ").strip()
            api_key_ref, notice = normalize_api_key_ref_input(
                raw_ref,
                default_ref=default_ref,
                secret_name=definition.default_api_key_env,
            )
            if notice:
                print(notice)
        elif definition.requires_api_key:
            raw_ref = input("Secret ref ou cle brute (env:NAME, file:/path ou secret:NAME) : ").strip()
            api_key_ref, notice = normalize_api_key_ref_input(
                raw_ref,
                secret_name=f"{definition.kind.upper().replace('-', '_')}_API_KEY",
            )
            if notice:
                print(notice)
    elif auth_type == AUTH_WEB:
        print("Auth web: un navigateur va s'ouvrir, puis BB9 attend le retour local.")
        try:
            token = ChatGPTOAuthFlow().run()
        except OAuthError as exc:
            print(f"Auth web echouee: {exc}")
            return
        token_path = default_web_token_path(provider_id)
        write_web_token(token_path, token)
        metadata = {
            "auth_method": "chatgpt_oauth_pkce",
            "token_path": str(token_path),
        }
        print(f"Auth web OK. Token local: {token_path}")

    draft = ProviderEntry(
        id=provider_id,
        name="",
        provider=definition.kind,
        auth_type=auth_type,
        base_url=base_url,
        api_key_ref=api_key_ref,
        metadata=metadata,
    )
    models = fetch_models_for_wizard(draft)
    model = choose_model(models)
    if not model:
        print("Ajout annule.")
        return

    config = store.load()
    default_name = f"{definition.kind}-{len(config.entries) + 1}"
    name = input(f"Nom [{default_name}] : ").strip() or default_name
    entry = ProviderEntry(
        id=draft.id,
        name=name,
        provider=draft.provider,
        auth_type=draft.auth_type,
        base_url=draft.base_url,
        api_key_ref=draft.api_key_ref,
        model=model,
        metadata=draft.metadata,
    )
    store.upsert(entry, active=True)
    cli.set_active_provider(entry)
    print(f"Provider actif: {entry.name} / {entry.model}")


def fetch_models_for_wizard(entry: ProviderEntry) -> list[str]:
    try:
        models = fetch_models(entry)
    except ModelFetchError as exc:
        print(f"Modeles non recuperes: {exc}")
        print("Tu peux saisir le modele manuellement, ou corriger la reference de secret puis relancer /model.")
        return []
    if models:
        print(f"{len(models)} modele(s) trouve(s).")
    return models


def choose_model(models: list[str], current: str = "") -> str:
    if not models:
        prompt = f"Modele [{current}] : " if current else "Modele : "
        return input(prompt).strip() or current

    filtered = models
    query = input("Filtre modele (Entree pour tout afficher) : ").strip().lower()
    if query:
        filtered = [model for model in models if query in model.lower()]
        if not filtered:
            print("Aucun modele ne correspond au filtre.")
            filtered = models

    shown = filtered[:40]
    for index, model in enumerate(shown, 1):
        print(f"{index}. {model}")
    if len(filtered) > len(shown):
        print(f"... {len(filtered) - len(shown)} autre(s), utilisez un filtre.")
    print("0. saisir manuellement")

    raw = input(f"Modele [1-{len(shown)}] : ").strip()
    if not raw and current:
        return current
    try:
        choice = int(raw)
    except ValueError:
        return ""
    if choice == 0:
        return input(f"Modele [{current}] : ").strip() or current
    if 1 <= choice <= len(shown):
        return shown[choice - 1]
    return ""
