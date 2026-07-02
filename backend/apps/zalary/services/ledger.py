from dataclasses import dataclass, field
import json
import os
from typing import Any, Sequence
from urllib.parse import urljoin

import requests

from .auth import DAML_PACKAGE_NAME, PLATFORM_CONFIG_CONTRACT_ID, LedgerAuthSettings, build_auth_headers
from .errors import LedgerNotImplementedError, LedgerSubmissionError, LedgerSyncError
from .templates import DEFAULT_PACKAGE_ID, DEFAULT_PACKAGE_NAME, ZALARY_CONFIG, TemplateRef


ACTIVE_CONTRACTS_ENDPOINT = "v2/state/active-contracts"
COMMAND_SUBMIT_AND_WAIT_ENDPOINT = "v2/commands/submit-and-wait"
EVENTS_BY_CONTRACT_ID_ENDPOINT = "v2/events/events-by-contract-id"
ZALARY_CONFIG_MODULE = "Zalary.Platform"
ZALARY_CONFIG_ENTITY = "ZalaryConfig"


@dataclass(frozen=True)
class CommandContext:
    act_as: Sequence[str]
    read_as: Sequence[str] = field(default_factory=list)
    command_id: str | None = None
    workflow_id: str | None = None


@dataclass(frozen=True)
class LedgerCommandResult:
    command_id: str
    update_id: str | None
    status: str
    raw_response: dict[str, Any] = field(default_factory=dict)


class LedgerClient:
    def __init__(self, settings: LedgerAuthSettings):
        self.settings = settings

    def query_active_contracts(
        self,
        *,
        template: TemplateRef | None = None,
        parties: Sequence[str] = (),
        offset: str | None = None,
        allow_wildcard_fallback: bool = True,
    ) -> list[dict[str, Any]]:
        if not parties:
            raise LedgerSyncError("At least one ledger party is required for active contract query.")

        query_offset = offset or self._current_ledger_end_offset_value()
        if template is None:
            return self._query_with_variants(
                variants=_wildcard_active_contract_request_bodies(parties=parties, offset=query_offset),
                failure_label="active-contract wildcard query",
            )

        exact_contracts = self._query_with_variants(
            variants=_template_active_contract_request_bodies(template=template, parties=parties, offset=query_offset),
            failure_label="active-contract template query",
            template=template,
        )
        if exact_contracts:
            return exact_contracts
        if not allow_wildcard_fallback:
            return []

        wildcard_contracts = self._query_with_variants(
            variants=_wildcard_active_contract_request_bodies(parties=parties, offset=query_offset),
            failure_label="active-contract wildcard fallback query",
        )
        return [contract for contract in wildcard_contracts if template_matches(contract, template)]

    def query_active_contracts_by_interface(
        self,
        *,
        interface_id: str,
        parties: Sequence[str] = (),
        offset: str | None = None,
    ) -> list[dict[str, Any]]:
        if not parties:
            raise LedgerSyncError("At least one ledger party is required for active contract interface query.")
        resolved_interface_id = (interface_id or "").strip()
        if not resolved_interface_id:
            raise LedgerSyncError("An interface id is required for active contract interface query.")

        query_offset = offset or self._current_ledger_end_offset_value()
        return self._query_with_variants(
            variants=_interface_active_contract_request_bodies(
                interface_id=resolved_interface_id,
                parties=parties,
                offset=query_offset,
            ),
            failure_label="active-contract interface query",
        )

    def query_active_contracts_by_template_identifier(
        self,
        *,
        package_id: str,
        module_name: str,
        entity_name: str,
        parties: Sequence[str] = (),
        offset: str | None = None,
    ) -> list[dict[str, Any]]:
        if not parties:
            raise LedgerSyncError("At least one ledger party is required for active contract template query.")
        if not (package_id and module_name and entity_name):
            raise LedgerSyncError("Package id, module name, and entity name are required for template query.")

        query_offset = offset or self._current_ledger_end_offset_value()
        return self._query_with_variants(
            variants=_raw_template_active_contract_request_bodies(
                package_id=package_id,
                module_name=module_name,
                entity_name=entity_name,
                parties=parties,
                offset=query_offset,
            ),
            failure_label="active-contract exact template query",
        )

    def query_zalary_config_contracts(self, *, parties: Sequence[str]) -> list[dict[str, Any]]:
        active_contract_error: LedgerSyncError | None = None

        try:
            contracts = self.query_active_contracts(template=ZALARY_CONFIG, parties=parties)
            if contracts:
                return contracts
        except LedgerSyncError as exc:
            active_contract_error = exc

        configured_contract_id = os.environ.get(PLATFORM_CONFIG_CONTRACT_ID, "").strip()
        if configured_contract_id:
            contract = self.fetch_visible_created_event_by_contract_id(
                contract_id=configured_contract_id,
                template=ZALARY_CONFIG,
                parties=parties,
            )
            if contract is not None:
                return [contract]

        if active_contract_error is not None:
            raise active_contract_error
        return []

    def diagnose_active_contracts(
        self,
        *,
        parties: Sequence[str],
        template: TemplateRef | None = ZALARY_CONFIG,
    ) -> dict[str, Any]:
        if not parties:
            raise LedgerSyncError("At least one ledger party is required for active contract diagnostics.")

        query_offset = self._current_ledger_end_offset_value()
        wildcard_variants = self._diagnose_active_contract_query_shapes(
            variants=_wildcard_active_contract_request_bodies(parties=parties, offset=query_offset),
        )
        wildcard_contracts = _contracts_from_best_variant(wildcard_variants)

        result: dict[str, Any] = {
            "wildcard_response_shape": _response_shape_from_best_variant(wildcard_variants),
            "wildcard_contracts": [
                safe_contract_metadata(contract)
                for contract in wildcard_contracts
            ],
            "query_shape_variants": [
                safe_query_variant_metadata(variant)
                for variant in wildcard_variants
            ],
            "active_at_offset_used": query_offset,
        }

        if template is not None:
            template_variants = self._diagnose_active_contract_query_shapes(
                variants=_template_active_contract_request_bodies(template=template, parties=parties, offset=query_offset),
            )
            template_contracts = _contracts_from_best_variant(template_variants)
            result["query_shape_variants"] = [
                safe_query_variant_metadata(variant)
                for variant in wildcard_variants + template_variants
            ]
            result["template_response_shape"] = _response_shape_from_best_variant(template_variants)
            result["template_contracts"] = [
                safe_contract_metadata(contract)
                for contract in template_contracts
            ]

        configured_contract_id = os.environ.get(PLATFORM_CONFIG_CONTRACT_ID, "").strip()
        if configured_contract_id:
            result["configured_contract_event_query"] = self.diagnose_contract_id_event_query(
                contract_id=configured_contract_id,
                parties=parties,
                template=template,
            )

        return result

    def fetch_visible_created_event_by_contract_id(
        self,
        *,
        contract_id: str,
        parties: Sequence[str],
        template: TemplateRef | None = None,
    ) -> dict[str, Any] | None:
        if not parties:
            raise LedgerSyncError("At least one ledger party is required for contract event query.")

        endpoint = urljoin(self.settings.ledger_api_url.rstrip("/") + "/", EVENTS_BY_CONTRACT_ID_ENDPOINT)
        body = _contract_id_event_request_body(contract_id=contract_id, parties=parties)
        response_body = self._post_json(endpoint, body, failure_label="events-by-contract-id query")

        if _response_has_archive_event(response_body):
            return None

        for contract in normalize_active_contracts_response(response_body):
            if contract.get("contract_id") != contract_id:
                continue
            if template is not None and not template_matches(contract, template):
                continue
            return contract
        return None

    def diagnose_contract_id_event_query(
        self,
        *,
        contract_id: str,
        parties: Sequence[str],
        template: TemplateRef | None = None,
    ) -> dict[str, Any]:
        endpoint = urljoin(self.settings.ledger_api_url.rstrip("/") + "/", EVENTS_BY_CONTRACT_ID_ENDPOINT)
        body = _contract_id_event_request_body(contract_id=contract_id, parties=parties)
        verify = self.settings.tls_ca_file or True

        try:
            response = requests.post(
                endpoint,
                headers={
                    **build_auth_headers(self.settings),
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=self.settings.timeout_seconds,
                verify=verify,
            )
        except requests.RequestException:
            return {
                "succeeded": False,
                "http_status": None,
                "error": "network_error",
                "contract_count": 0,
                "archived_present": False,
                "contract": None,
            }

        if response.status_code >= 400:
            return {
                "succeeded": False,
                "http_status": response.status_code,
                "error": "http_error",
                "error_detail": _safe_response_text(response.text),
                "contract_count": 0,
                "archived_present": False,
                "contract": None,
            }

        try:
            response_body = _parse_json_response(response.text)
        except LedgerSyncError:
            return {
                "succeeded": False,
                "http_status": response.status_code,
                "error": "invalid_json",
                "contract_count": 0,
                "archived_present": False,
                "contract": None,
            }

        contracts = [
            contract
            for contract in normalize_active_contracts_response(response_body)
            if contract.get("contract_id") == contract_id
            and (template is None or template_matches(contract, template))
        ]
        archived_present = _response_has_archive_event(response_body)
        return {
            "succeeded": True,
            "http_status": response.status_code,
            "error": "",
            "contract_count": len(contracts),
            "archived_present": archived_present,
            "contract": safe_contract_metadata(contracts[0]) if contracts and not archived_present else None,
        }

    def _diagnose_active_contract_query_shapes(
        self,
        *,
        variants: list[tuple[str, dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        endpoint = urljoin(self.settings.ledger_api_url.rstrip("/") + "/", ACTIVE_CONTRACTS_ENDPOINT)
        results = []

        for variant_name, body in variants:
            result = self._post_json_diagnostic(endpoint, body, include_contracts=True)
            result["name"] = variant_name
            results.append(result)

        return results

    def fetch_contract_by_id(
        self,
        *,
        contract_id: str,
        template: TemplateRef,
        parties: Sequence[str],
    ) -> dict[str, Any] | None:
        # TODO: Implement ContractId fetch with actAs/readAs visibility.
        raise LedgerNotImplementedError("Contract fetch is not implemented yet.")

    def submit_exercise(
        self,
        *,
        context: CommandContext,
        template: TemplateRef,
        contract_id: str,
        choice: str,
        argument: dict[str, Any],
    ) -> LedgerCommandResult:
        if not context.act_as:
            raise LedgerSubmissionError("At least one actAs party is required for command submission.")
        if not context.command_id:
            raise LedgerSubmissionError("A command_id is required for command submission.")

        endpoint = urljoin(self.settings.ledger_api_url.rstrip("/") + "/", COMMAND_SUBMIT_AND_WAIT_ENDPOINT)
        body = _exercise_command_request_body(
            context=context,
            template=template,
            contract_id=contract_id,
            choice=choice,
            argument=argument,
        )
        response_body = self._post_json_for_submission(
            endpoint,
            body,
            failure_label="exercise command submission",
        )
        update_id = _find_first_string(response_body, ("updateId", "update_id", "transactionId", "transaction_id"))
        return LedgerCommandResult(
            command_id=context.command_id,
            update_id=update_id,
            status="succeeded",
            raw_response=_sanitize_command_response(response_body),
        )

    def submit_exercise_interface(
        self,
        *,
        context: CommandContext,
        interface_id: str,
        contract_id: str,
        choice: str,
        argument: dict[str, Any],
        disclosed_contracts: Sequence[dict[str, Any]] = (),
    ) -> LedgerCommandResult:
        if not context.act_as:
            raise LedgerSubmissionError("At least one actAs party is required for command submission.")
        if not context.command_id:
            raise LedgerSubmissionError("A command_id is required for command submission.")
        resolved_interface_id = (interface_id or "").strip()
        if not resolved_interface_id:
            raise LedgerSubmissionError("An interface id is required for interface choice submission.")

        endpoint = urljoin(self.settings.ledger_api_url.rstrip("/") + "/", COMMAND_SUBMIT_AND_WAIT_ENDPOINT)
        body = _exercise_command_request_body_for_identifier(
            context=context,
            template_or_interface_id=resolved_interface_id,
            contract_id=contract_id,
            choice=choice,
            argument=argument,
            disclosed_contracts=disclosed_contracts,
        )
        response_body = self._post_json_for_submission(
            endpoint,
            body,
            failure_label="interface exercise command submission",
        )
        update_id = _find_first_string(response_body, ("updateId", "update_id", "transactionId", "transaction_id"))
        return LedgerCommandResult(
            command_id=context.command_id,
            update_id=update_id,
            status="succeeded",
            raw_response=_sanitize_command_response(response_body),
        )

    def submit_create(
        self,
        *,
        context: CommandContext,
        template: TemplateRef,
        payload: dict[str, Any],
    ) -> LedgerCommandResult:
        # TODO: Submit a create command if future backend flows need direct creates.
        raise LedgerNotImplementedError("Create command submission is not implemented yet.")

    def stream_updates(
        self,
        *,
        parties: Sequence[str],
        begin_offset: str | None = None,
    ) -> list[dict[str, Any]]:
        # TODO: Poll or stream transaction updates and return created/archived events.
        raise LedgerNotImplementedError("Ledger update streaming is not implemented yet.")

    def get_current_ledger_offset(self) -> dict[str, Any]:
        endpoint = urljoin(self.settings.ledger_api_url.rstrip("/") + "/", "v2/state/ledger-end")
        verify = self.settings.tls_ca_file or True

        try:
            response = requests.get(
                endpoint,
                headers=build_auth_headers(self.settings),
                timeout=self.settings.timeout_seconds,
                verify=verify,
            )
        except requests.RequestException as exc:
            raise LedgerSyncError("Ledger API ledger-end request failed due to a network error.") from exc

        if response.status_code >= 400:
            raise LedgerSyncError(
                f"Ledger API ledger-end request failed with HTTP {response.status_code}."
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise LedgerSyncError("Ledger API ledger-end response was not valid JSON.") from exc

        if isinstance(body, dict):
            return body
        return {"ledger_end": body}

    def _current_ledger_end_offset_value(self) -> str | None:
        ledger_end = self.get_current_ledger_offset()
        value = ledger_end.get("offset") or ledger_end.get("ledgerEnd") or ledger_end.get("ledger_end")
        if value is None:
            return None
        return str(value)

    def _post_json(self, endpoint: str, body: dict[str, Any], *, failure_label: str) -> dict[str, Any] | list[Any]:
        verify = self.settings.tls_ca_file or True

        try:
            response = requests.post(
                endpoint,
                headers={
                    **build_auth_headers(self.settings),
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=self.settings.timeout_seconds,
                verify=verify,
            )
        except requests.RequestException as exc:
            raise LedgerSyncError(f"Ledger API {failure_label} failed due to a network error.") from exc

        if response.status_code >= 400:
            raise LedgerSyncError(
                f"Ledger API {failure_label} failed with HTTP {response.status_code}."
            )

        try:
            return _parse_json_response(response.text)
        except LedgerSyncError as exc:
            raise LedgerSyncError(
                f"Ledger API {failure_label} response was not valid JSON."
            ) from exc

    def _post_json_for_submission(
        self,
        endpoint: str,
        body: dict[str, Any],
        *,
        failure_label: str,
    ) -> dict[str, Any] | list[Any]:
        verify = self.settings.tls_ca_file or True

        try:
            response = requests.post(
                endpoint,
                headers={
                    **build_auth_headers(self.settings),
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=self.settings.timeout_seconds,
                verify=verify,
            )
        except requests.RequestException as exc:
            raise LedgerSubmissionError(
                f"Ledger API {failure_label} failed due to a network error."
            ) from exc

        if response.status_code >= 400:
            raise LedgerSubmissionError(
                f"Ledger API {failure_label} failed with HTTP {response.status_code}: "
                f"{_safe_response_text(response.text)}"
            )

        try:
            return _parse_json_response(response.text)
        except LedgerSyncError as exc:
            raise LedgerSubmissionError(
                f"Ledger API {failure_label} response was not valid JSON."
            ) from exc

    def _query_with_variants(
        self,
        *,
        variants: list[tuple[str, dict[str, Any]]],
        failure_label: str,
        template: TemplateRef | None = None,
    ) -> list[dict[str, Any]]:
        endpoint = urljoin(self.settings.ledger_api_url.rstrip("/") + "/", ACTIVE_CONTRACTS_ENDPOINT)
        first_successful_contracts: list[dict[str, Any]] | None = None
        errors: list[str] = []

        for variant_name, body in variants:
            result = self._post_json_diagnostic(endpoint, body, include_contracts=True)
            if not result["succeeded"]:
                errors.append(f"{variant_name}:{result.get('error') or result.get('http_status')}")
                continue

            contracts = result.get("_contracts") or []
            if template is not None:
                contracts = [contract for contract in contracts if template_matches(contract, template)]

            if contracts:
                return contracts
            if first_successful_contracts is None:
                first_successful_contracts = contracts

        if first_successful_contracts is not None:
            return first_successful_contracts

        details = ", ".join(errors) if errors else "no request variants were available"
        raise LedgerSyncError(f"Ledger API {failure_label} failed for all request shapes: {details}.")

    def _post_json_diagnostic(
        self,
        endpoint: str,
        body: dict[str, Any],
        *,
        include_contracts: bool = False,
    ) -> dict[str, Any]:
        verify = self.settings.tls_ca_file or True

        try:
            response = requests.post(
                endpoint,
                headers={
                    **build_auth_headers(self.settings),
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=self.settings.timeout_seconds,
                verify=verify,
            )
        except requests.RequestException:
            result = {
                "succeeded": False,
                "http_status": None,
                "error": "network_error",
                "response_shape": None,
                "contract_count": 0,
                "template_ids": [],
            }
            if include_contracts:
                result["_contracts"] = []
            return result

        if response.status_code >= 400:
            result = {
                "succeeded": False,
                "http_status": response.status_code,
                "error": "http_error",
                "error_detail": _safe_response_text(response.text),
                "response_shape": None,
                "contract_count": 0,
                "template_ids": [],
            }
            if include_contracts:
                result["_contracts"] = []
            return result

        try:
            body = _parse_json_response(response.text)
        except LedgerSyncError:
            result = {
                "succeeded": False,
                "http_status": response.status_code,
                "error": "invalid_json",
                "response_shape": None,
                "contract_count": 0,
                "template_ids": [],
            }
            if include_contracts:
                result["_contracts"] = []
            return result

        contracts = normalize_active_contracts_response(body)
        template_ids = sorted(
            {
                template_display_text(contract.get("template_id") or {})
                for contract in contracts
                if template_display_text(contract.get("template_id") or {})
            }
        )
        result = {
            "succeeded": True,
            "http_status": response.status_code,
            "error": "",
            "response_shape": safe_response_shape(body),
            "contract_count": len(contracts),
            "template_ids": template_ids,
        }
        if include_contracts:
            result["_contracts"] = contracts
        return result

    def _active_contracts_request_body(
        self,
        *,
        template: TemplateRef | None,
        parties: Sequence[str],
        offset: str | None,
    ) -> dict[str, Any]:
        variants = (
            _template_active_contract_request_bodies(template=template, parties=parties, offset=offset)
            if template is not None
            else _wildcard_active_contract_request_bodies(parties=parties, offset=offset)
        )
        return variants[0][1]


def _wildcard_active_contract_request_bodies(
    *,
    parties: Sequence[str],
    offset: str | None,
) -> list[tuple[str, dict[str, Any]]]:
    return _active_contract_variant_bodies(
        parties=parties,
        filters=[
            (
                "wildcard_constructor_include_blob",
                _constructor_wildcard_filter(include_created_event_blob=True),
            ),
            (
                "wildcard_constructor_no_blob",
                _constructor_wildcard_filter(include_created_event_blob=False),
            ),
            (
                "wildcard_identifier_include_blob",
                {"identifierFilter": {"wildcardFilter": {"includeCreatedEventBlob": True}}},
            ),
            (
                "wildcard_identifier_no_blob",
                {"identifierFilter": {"wildcardFilter": {}}},
            ),
            (
                "wildcard_direct_include_blob",
                {"wildcardFilter": {"includeCreatedEventBlob": True}},
            ),
        ],
        offset=offset,
    )


def _template_active_contract_request_bodies(
    *,
    template: TemplateRef,
    parties: Sequence[str],
    offset: str | None,
) -> list[tuple[str, dict[str, Any]]]:
    package_id_template_id = template.identifier(package_id=DEFAULT_PACKAGE_ID)
    package_id_template_text = _template_package_id_text(template)
    package_name_template_text = _template_package_name_text(template)
    return _active_contract_variant_bodies(
        parties=parties,
        filters=[
            (
                "template_constructor_package_name_include_blob",
                _constructor_template_filter(
                    template_id=package_name_template_text,
                    include_created_event_blob=True,
                ),
            ),
            (
                "template_constructor_package_name_no_blob",
                _constructor_template_filter(
                    template_id=package_name_template_text,
                    include_created_event_blob=False,
                ),
            ),
            (
                "template_constructor_exact_package_id_include_blob",
                _constructor_template_filter(
                    template_id=package_id_template_text,
                    include_created_event_blob=True,
                ),
            ),
            (
                "template_constructor_exact_package_id_no_blob",
                _constructor_template_filter(
                    template_id=package_id_template_text,
                    include_created_event_blob=False,
                ),
            ),
            (
                "template_identifier_exact_package_id_include_blob",
                {
                    "identifierFilter": {
                        "templateFilter": {
                            "templateId": package_id_template_id,
                            "includeCreatedEventBlob": True,
                        }
                    }
                },
            ),
            (
                "template_identifier_exact_package_id_no_blob",
                {
                    "identifierFilter": {
                        "templateFilter": {
                            "templateId": package_id_template_id,
                        }
                    }
                },
            ),
        ],
        offset=offset,
    )


def _interface_active_contract_request_bodies(
    *,
    interface_id: str,
    parties: Sequence[str],
    offset: str | None,
) -> list[tuple[str, dict[str, Any]]]:
    return _active_contract_variant_bodies(
        parties=parties,
        filters=[
            (
                "interface_constructor_include_view_blob",
                _constructor_interface_filter(
                    interface_id=interface_id,
                    include_interface_view=True,
                    include_created_event_blob=True,
                ),
            ),
            (
                "interface_constructor_include_view_no_blob",
                _constructor_interface_filter(
                    interface_id=interface_id,
                    include_interface_view=True,
                    include_created_event_blob=False,
                ),
            ),
            (
                "interface_identifier_include_view_blob",
                {
                    "identifierFilter": {
                        "interfaceFilter": {
                            "interfaceId": interface_id,
                            "includeInterfaceView": True,
                            "includeCreatedEventBlob": True,
                        }
                    }
                },
            ),
            (
                "interface_identifier_include_view_no_blob",
                {
                    "identifierFilter": {
                        "interfaceFilter": {
                            "interfaceId": interface_id,
                            "includeInterfaceView": True,
                        }
                    }
                },
            ),
            (
                "interface_direct_include_view_blob",
                {
                    "interfaceFilter": {
                        "interfaceId": interface_id,
                        "includeInterfaceView": True,
                        "includeCreatedEventBlob": True,
                    }
                },
            ),
        ],
        offset=offset,
    )


def _raw_template_active_contract_request_bodies(
    *,
    package_id: str,
    module_name: str,
    entity_name: str,
    parties: Sequence[str],
    offset: str | None,
) -> list[tuple[str, dict[str, Any]]]:
    template_id = {
        "packageId": package_id,
        "moduleName": module_name,
        "entityName": entity_name,
    }
    template_text = f"{package_id}:{module_name}:{entity_name}"
    return _active_contract_variant_bodies(
        parties=parties,
        filters=[
            (
                "template_identifier_raw_package_id_include_blob",
                {
                    "identifierFilter": {
                        "templateFilter": {
                            "templateId": template_id,
                            "includeCreatedEventBlob": True,
                        }
                    }
                },
            ),
            (
                "template_identifier_raw_package_id_no_blob",
                {
                    "identifierFilter": {
                        "templateFilter": {
                            "templateId": template_id,
                        }
                    }
                },
            ),
            (
                "template_constructor_raw_package_id_include_blob",
                _constructor_template_filter(
                    template_id=template_text,
                    include_created_event_blob=True,
                ),
            ),
            (
                "template_constructor_raw_package_id_no_blob",
                _constructor_template_filter(
                    template_id=template_text,
                    include_created_event_blob=False,
                ),
            ),
        ],
        offset=offset,
    )


def _constructor_wildcard_filter(*, include_created_event_blob: bool) -> dict[str, Any]:
    value: dict[str, Any] = {}
    if include_created_event_blob:
        value["includeCreatedEventBlob"] = True
    return {"identifierFilter": {"WildcardFilter": {"value": value}}}


def _constructor_template_filter(*, template_id: str, include_created_event_blob: bool) -> dict[str, Any]:
    value: dict[str, Any] = {"templateId": template_id}
    if include_created_event_blob:
        value["includeCreatedEventBlob"] = True
    return {"identifierFilter": {"TemplateFilter": {"value": value}}}


def _constructor_interface_filter(
    *,
    interface_id: str,
    include_interface_view: bool,
    include_created_event_blob: bool,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "interfaceId": interface_id,
        "includeInterfaceView": include_interface_view,
    }
    if include_created_event_blob:
        value["includeCreatedEventBlob"] = True
    return {"identifierFilter": {"InterfaceFilter": {"value": value}}}


def _template_package_id_text(template: TemplateRef) -> str:
    return f"{DEFAULT_PACKAGE_ID}:{template.module_name}:{template.entity_name}"


def _template_package_name_text(template: TemplateRef) -> str:
    package_name = os.environ.get(DAML_PACKAGE_NAME, "").strip() or DEFAULT_PACKAGE_NAME
    return f"#{package_name}:{template.module_name}:{template.entity_name}"


def _contract_id_event_request_body(
    *,
    contract_id: str,
    parties: Sequence[str],
) -> dict[str, Any]:
    wildcard_filter = _constructor_wildcard_filter(include_created_event_blob=False)
    return {
        "contractId": contract_id,
        "requestingParties": list(parties),
        "eventFormat": {
            "filtersByParty": {
                party: {"cumulative": [wildcard_filter]}
                for party in parties
            },
            "verbose": True,
        },
    }


def _exercise_command_request_body(
    *,
    context: CommandContext,
    template: TemplateRef,
    contract_id: str,
    choice: str,
    argument: dict[str, Any],
) -> dict[str, Any]:
    return _exercise_command_request_body_for_identifier(
        context=context,
        template_or_interface_id=_template_package_name_text(template),
        contract_id=contract_id,
        choice=choice,
        argument=argument,
    )


def _exercise_command_request_body_for_identifier(
    *,
    context: CommandContext,
    template_or_interface_id: str,
    contract_id: str,
    choice: str,
    argument: dict[str, Any],
    disclosed_contracts: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "commands": [
            {
                "ExerciseCommand": {
                    "templateId": template_or_interface_id,
                    "contractId": contract_id,
                    "choice": choice,
                    "choiceArgument": argument,
                }
            }
        ],
        "commandId": context.command_id,
        "actAs": list(context.act_as),
    }
    if context.read_as:
        body["readAs"] = list(context.read_as)
    if context.workflow_id:
        body["workflowId"] = context.workflow_id
    if disclosed_contracts:
        body["disclosedContracts"] = list(disclosed_contracts)
    return body


def _active_contract_variant_bodies(
    *,
    parties: Sequence[str],
    filters: list[tuple[str, dict[str, Any]]],
    offset: str | None,
) -> list[tuple[str, dict[str, Any]]]:
    variants: list[tuple[str, dict[str, Any]]] = []
    for filter_name, identifier_filter in filters:
        cumulative_by_party = {
            party: {"cumulative": [identifier_filter]}
            for party in parties
        }
        filter_body: dict[str, Any] = {
            "filter": {"filtersByParty": cumulative_by_party},
            "verbose": True,
        }
        event_format_body: dict[str, Any] = {
            "eventFormat": {
                "filtersByParty": cumulative_by_party,
                "verbose": True,
            },
        }
        if offset:
            filter_body["activeAtOffset"] = offset
            event_format_body["activeAtOffset"] = offset

        variants.append((f"filter_{filter_name}", filter_body))
        variants.append((f"event_format_{filter_name}", event_format_body))
    return variants


def _contracts_from_best_variant(variants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for variant in variants:
        contracts = variant.get("_contracts") or []
        if contracts:
            return contracts
    for variant in variants:
        if variant.get("succeeded"):
            return variant.get("_contracts") or []
    return []


def _response_shape_from_best_variant(variants: list[dict[str, Any]]) -> dict[str, Any] | None:
    for variant in variants:
        if variant.get("contract_count", 0) > 0:
            return variant.get("response_shape")
    for variant in variants:
        if variant.get("succeeded"):
            return variant.get("response_shape")
    return None


def _parse_json_response(text: str) -> dict[str, Any] | list[Any]:
    try:
        return json.loads(text)
    except ValueError:
        values = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                values.append(json.loads(stripped))
            except ValueError as exc:
                raise LedgerSyncError("Ledger API response was not valid JSON.") from exc
        return values


def _safe_response_text(text: str) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) > 160:
        return f"{cleaned[:160]}..."
    return cleaned


def _find_first_string(value: Any, keys: tuple[str, ...]) -> str | None:
    if isinstance(value, list):
        for item in value:
            found = _find_first_string(item, keys)
            if found:
                return found
        return None
    if not isinstance(value, dict):
        return None

    for key in keys:
        item = value.get(key)
        if item is not None:
            return str(item)

    for item in value.values():
        found = _find_first_string(item, keys)
        if found:
            return found
    return None


def _sanitize_command_response(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return _sanitize_json_value(value)
    if isinstance(value, list):
        return {"items": [_sanitize_json_value(item) for item in value]}
    return {"value": value}


def _sanitize_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            lowered = key.lower()
            if "token" in lowered or "secret" in lowered or "authorization" in lowered:
                sanitized[key] = "[redacted]"
            else:
                sanitized[key] = _sanitize_json_value(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_json_value(item) for item in value]
    return value


def normalize_active_contracts_response(body: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    contracts = []
    for item in _iter_contract_items(body):
        event = _extract_created_event(item)
        if event is None:
            continue
        normalized = _normalize_created_event(event, item)
        if normalized.get("contract_id"):
            contracts.append(normalized)
    return contracts


def _response_has_archive_event(body: dict[str, Any] | list[Any]) -> bool:
    if isinstance(body, list):
        return any(_response_has_archive_event(item) for item in body)
    if not isinstance(body, dict):
        return False

    for key in ("archived", "archivedEvent", "archiveEvent", "archived_event", "archive_event"):
        value = body.get(key)
        if value:
            return True

    for key in ("events", "results", "items", "entries", "contractEntry", "contract_entry"):
        value = body.get(key)
        if value is not None and _response_has_archive_event(value):
            return True
    for key in ("JsArchivedEvent", "JsArchivedContract", "jsArchivedEvent", "jsArchivedContract"):
        if body.get(key):
            return True
    return False


def safe_contract_metadata(contract: dict[str, Any]) -> dict[str, Any]:
    template_info = contract.get("template_id") or {}
    payload = contract.get("payload") or {}
    package_name = contract.get("package_name") or _package_name_from_template_info(template_info)
    interface_views = contract.get("interface_views") or {}
    return {
        "contract_id": contract.get("contract_id") or "",
        "template_id": template_display_text(template_info),
        "package_name": package_name,
        "module_name": template_info.get("module_name") or "",
        "entity_name": template_info.get("entity_name") or "",
        "signatories": contract.get("signatories") or [],
        "observers": contract.get("observers") or [],
        "payload_keys": sorted(payload.keys()) if isinstance(payload, dict) else [],
        "interface_ids": sorted(interface_views.keys()) if isinstance(interface_views, dict) else [],
    }


def safe_response_shape(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        first = value[0] if value else None
        return {
            "type": "list",
            "length": len(value),
            "first_item": safe_response_shape(first) if first is not None else None,
        }
    if isinstance(value, dict):
        return {
            "type": "dict",
            "keys": sorted(value.keys()),
        }
    return {"type": type(value).__name__}


def safe_query_variant_metadata(variant: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "name": variant.get("name", ""),
        "succeeded": bool(variant.get("succeeded")),
        "http_status": variant.get("http_status"),
        "contract_count": variant.get("contract_count") or 0,
        "error": variant.get("error", ""),
    }
    if variant.get("template_ids"):
        metadata["template_ids"] = variant["template_ids"]
    if variant.get("error_detail"):
        metadata["error_detail"] = variant["error_detail"]
    return metadata


def template_matches(contract: dict[str, Any], template: TemplateRef) -> bool:
    template_info = contract.get("template_id") or {}
    template_text = template_display_text(template_info)
    return (
        template_info.get("module_name") == template.module_name
        and template_info.get("entity_name") == template.entity_name
    ) or (
        template.module_name in template_text
        and template.entity_name in template_text
    )


def template_display_text(template_info: dict[str, str]) -> str:
    package_id = template_info.get("package_id") or ""
    module_name = template_info.get("module_name") or ""
    entity_name = template_info.get("entity_name") or ""
    parts = [part for part in (package_id, module_name, entity_name) if part]
    return ":".join(parts)


def _package_name_from_template_info(template_info: dict[str, str]) -> str:
    package_id = template_info.get("package_id") or ""
    if package_id.startswith("#"):
        return package_id[1:]
    return ""


def _iter_contract_items(value: Any):
    if isinstance(value, list):
        for item in value:
            yield from _iter_contract_items(item)
        return

    if not isinstance(value, dict):
        return

    if _extract_created_event(value) is not None:
        yield value
        return

    for key in (
        "contracts",
        "contract",
        "activeContracts",
        "activeContract",
        "active_contract",
        "contractEntries",
        "contractEntry",
        "contract_entry",
        "JsActiveContract",
        "jsActiveContract",
        "createdEvents",
        "created_events",
        "createEvents",
        "create_events",
        "events",
        "results",
        "items",
        "active_contracts",
        "entries",
        "created",
        "createdEvent",
        "createEvent",
    ):
        nested = value.get(key)
        if nested is not None:
            yield from _iter_contract_items(nested)


def _extract_created_event(item: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("createdEvent", "created_event", "createEvent", "create_event"):
        value = item.get(key)
        if isinstance(value, dict):
            return value

    for key in ("created", "event"):
        value = item.get(key)
        if isinstance(value, dict):
            nested = _extract_created_event(value)
            if nested is not None:
                return nested
            if "contractId" in value or "contract_id" in value:
                return value

    for key in (
        "activeContract",
        "active_contract",
        "contractEntry",
        "contract_entry",
        "contract",
        "JsActiveContract",
        "jsActiveContract",
    ):
        value = item.get(key)
        if isinstance(value, dict):
            nested = _extract_created_event(value)
            if nested is not None:
                return nested
            if "contractId" in value or "contract_id" in value:
                return value

    if "contractId" in item or "contract_id" in item:
        return item
    return None


def _normalize_created_event(event: dict[str, Any], source_item: dict[str, Any]) -> dict[str, Any]:
    template_info = _normalize_template_id(
        event.get("templateId")
        or event.get("template_id")
        or event.get("template")
        or event.get("identifier")
    )
    payload = _normalize_daml_value(
        event.get("createArgument")
        or event.get("create_argument")
        or event.get("createArguments")
        or event.get("create_arguments")
        or event.get("createdArguments")
        or event.get("created_arguments")
        or event.get("payload")
        or event.get("argument")
        or event.get("arguments")
        or {}
    )

    return {
        "contract_id": event.get("contractId") or event.get("contract_id") or "",
        "template_id": template_info,
        "package_name": event.get("packageName") or event.get("package_name") or _package_name_from_template_info(template_info),
        "payload": payload,
        "interface_views": _normalize_interface_views(event, source_item),
        "contract_key": _normalize_daml_value(event.get("contractKey") or event.get("contract_key")),
        "signatories": event.get("signatories") or [],
        "observers": event.get("observers") or [],
        "created_event_blob": event.get("createdEventBlob") or "",
        "created_at": (
            event.get("createdAt")
            or event.get("created_at")
            or source_item.get("createdAt")
            or source_item.get("created_at")
        ),
        "ledger_offset": (
            event.get("offset")
            or source_item.get("offset")
            or source_item.get("ledgerOffset")
            or source_item.get("ledger_offset")
        ),
        "raw": source_item,
    }


def _normalize_interface_views(event: dict[str, Any], source_item: dict[str, Any]) -> dict[str, Any]:
    raw_views = (
        event.get("interfaceViews")
        or event.get("interface_views")
        or event.get("interfaceView")
        or event.get("interface_view")
        or source_item.get("interfaceViews")
        or source_item.get("interface_views")
        or source_item.get("interfaceView")
        or source_item.get("interface_view")
        or {}
    )
    if not raw_views:
        return {}

    if isinstance(raw_views, dict):
        normalized: dict[str, Any] = {}
        for key, value in raw_views.items():
            if key in {"interfaceId", "interface_id"}:
                continue
            if isinstance(value, dict) and ("interfaceId" in value or "interface_id" in value):
                interface_id = str(value.get("interfaceId") or value.get("interface_id") or key)
                view_value = _interface_view_payload(value)
                normalized[interface_id] = _normalize_daml_value(view_value)
            else:
                normalized[str(key)] = _normalize_daml_value(_interface_view_payload(value))
        if normalized:
            return normalized

    if isinstance(raw_views, list):
        normalized = {}
        for item in raw_views:
            if not isinstance(item, dict):
                continue
            interface_id = item.get("interfaceId") or item.get("interface_id") or item.get("id")
            if not interface_id:
                continue
            view_value = _interface_view_payload(item)
            normalized[str(interface_id)] = _normalize_daml_value(view_value)
        return normalized

    return {}


def _interface_view_payload(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    for wrapper_key in ("viewStatus", "view_status", "interfaceView", "interface_view"):
        wrapped = value.get(wrapper_key)
        if wrapped is not None:
            return _interface_view_payload(wrapped)
    for payload_key in ("viewValue", "view_value", "value", "view"):
        payload = value.get(payload_key)
        if payload is not None:
            return _interface_view_payload(payload)
    return value


def _normalize_template_id(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {
            "package_id": value.get("packageId") or value.get("package_id") or "",
            "module_name": value.get("moduleName") or value.get("module_name") or value.get("module") or "",
            "entity_name": value.get("entityName") or value.get("entity_name") or value.get("entity") or "",
        }

    if isinstance(value, str):
        parts = value.split(":")
        if len(parts) >= 3:
            return {
                "package_id": ":".join(parts[:-2]),
                "module_name": parts[-2],
                "entity_name": parts[-1],
            }

    return {"package_id": "", "module_name": "", "entity_name": ""}


def _normalize_daml_value(value: Any) -> Any:
    if isinstance(value, list):
        return [_normalize_daml_value(item) for item in value]

    if not isinstance(value, dict):
        return value

    if set(value.keys()) == {"value"}:
        return _normalize_daml_value(value["value"])

    for scalar_key in ("party", "text", "bool", "timestamp", "date", "numeric", "decimal", "int64"):
        if set(value.keys()) == {scalar_key}:
            return value[scalar_key]

    if "record" in value and isinstance(value["record"], dict):
        return _normalize_daml_value(value["record"])

    fields = value.get("fields")
    if isinstance(fields, list):
        normalized: dict[str, Any] = {}
        for field in fields:
            if isinstance(field, dict) and "label" in field:
                normalized[field["label"]] = _normalize_daml_value(field.get("value"))
        return normalized

    if isinstance(fields, dict):
        return {key: _normalize_daml_value(item) for key, item in fields.items()}

    if "list" in value:
        return _normalize_daml_value(value["list"])

    if "variant" in value and isinstance(value["variant"], dict):
        variant = value["variant"]
        return {
            "tag": variant.get("constructor") or variant.get("tag"),
            "value": _normalize_daml_value(variant.get("value")),
        }

    return {key: _normalize_daml_value(item) for key, item in value.items()}
