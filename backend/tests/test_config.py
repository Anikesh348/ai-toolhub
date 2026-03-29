from app.utils.config import parse_cors_allowed_origins


def test_parse_cors_origins_supports_host_port_without_scheme() -> None:
    parsed = parse_cors_allowed_origins("pi-purva:3005,pi-purva:3006")

    assert parsed == [
        "http://pi-purva:3005",
        "https://pi-purva:3005",
        "http://pi-purva:3006",
        "https://pi-purva:3006",
    ]


def test_parse_cors_origins_normalizes_trailing_paths_and_slashes() -> None:
    parsed = parse_cors_allowed_origins("http://pi-purva:3005/, https://pi-purva:3006/health")

    assert parsed == [
        "http://pi-purva:3005",
        "https://pi-purva:3006",
    ]
