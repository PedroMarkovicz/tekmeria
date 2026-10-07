import medcite


def test_version_and_contract_version() -> None:
    assert medcite.__version__ == "0.1.0"
    assert medcite.CONTRACT_VERSION == "0.1.0"
