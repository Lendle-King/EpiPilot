from epipilot.research.frontier import InputFile, MeasurementContract, Purpose, Rule


def _legacy_shaped_contract(**updates: object) -> MeasurementContract:
    values: dict[str, object] = {
        "proposal_id": "legacy-contract-example",
        "purpose": Purpose.EXPLORE,
        "adapter": "missingness",
        "git_revision": "91bfa7ecd9339ae3ca51226b57559c3cfb0237b1",
        "inputs": (InputFile(path="data/synthetic.csv", sha256="a" * 64),),
        "boundary_date": "2019-01-01",
        "cutoff_date": "2026-10-16",
        "rules": (Rule(metric="before_share_of_missing", minimum=0.99),),
    }
    values.update(updates)
    return MeasurementContract.model_validate(values)


def test_default_prospective_fields_preserve_legacy_contract_fingerprint() -> None:
    contract = _legacy_shaped_contract()

    assert (
        contract.fingerprint() == "57c1535d6a291c470c569d3c7b4d79c535a60cf6577e1c8daeaf885183119647"
    )


def test_non_default_prospective_fields_remain_part_of_contract_fingerprint() -> None:
    legacy = _legacy_shaped_contract()
    prospective = _legacy_shaped_contract(prospective=True, data_source="SZSE PCF feed")

    assert prospective.fingerprint() != legacy.fingerprint()
