# Omniverse Storage API protos

The `.proto` files under `nvidia/` are copied unchanged from NVIDIA's
[usd-content-workspace](https://github.com/NVIDIA-Omniverse/usd-content-workspace) repository, `storage/api/proto/`, at
commit `a52c5d7bb31fe1ce5db70b2ad16fe30219496adf` (2026-10-20), under the Apache License 2.0 in `LICENSE` here. Only the
`capabilities`, `filefolder` and `fileobject` services are included, in their `v1alpha` and `v1beta` versions: the
Omniverse client library of Kit 110.4 (Isaac Sim 6.2) calls the `v1alpha` services, and `v1beta` is served beside them.

`../_proto/` holds the Python modules generated from them, so installing the connector needs no code generation. They
were generated with `grpcio-tools` 1.85.4 (protobuf 7.35.1 generated code), which requires the runtime versions the
connector's `requirements-storage.txt` pins. To regenerate after changing the protos or the pins, from this directory:

```bash
python -m grpc_tools.protoc -I . --python_out=../_proto --grpc_python_out=../_proto $(find nvidia -name "*.proto" | sort)
```
