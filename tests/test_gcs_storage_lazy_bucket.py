"""Achado #19/M2: GCSStorage NÃO deve fazer exists()/create_bucket no construtor.

Verifica que instanciar GCSStorage:
- usa self.client.bucket(name) (referência lazy, sem round-trip);
- NÃO chama bucket.exists() nem client.create_bucket() (sem exigir
  storage.buckets.create na SA).

O cliente do GCS é mockado — nenhum acesso de rede.
"""
from unittest.mock import MagicMock, patch


def test_construtor_nao_cria_bucket():
    with patch("src.storage.gcs_storage.storage.Client") as MockClient:
        client = MockClient.return_value
        bucket = MagicMock()
        client.bucket.return_value = bucket

        from src.storage.gcs_storage import GCSStorage
        store = GCSStorage(bucket_name="meu-bucket")

        # bucket() é a única chamada — lazy, sem rede.
        client.bucket.assert_called_once_with("meu-bucket")
        # NÃO verifica existência nem cria bucket.
        bucket.exists.assert_not_called()
        client.create_bucket.assert_not_called()
        assert store.bucket is bucket
