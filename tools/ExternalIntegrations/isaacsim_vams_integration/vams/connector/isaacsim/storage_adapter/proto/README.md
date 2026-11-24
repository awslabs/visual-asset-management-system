# Omniverse Storage API protos

The `.proto` files under `nvidia/` are copied unchanged from NVIDIA's
[usd-content-workspace](https://github.com/NVIDIA-Omniverse/usd-content-workspace) repository at commit
`a52c5d7bb31fe1ce5db70b2ad16fe30219496adf` (2026-10-20), under the Apache License 2.0 in `LICENSE` here:

-   `nvidia/omniverse/storage/`, from `storage/api/proto/`: the `capabilities`, `filefolder` and `fileobject` services in
    their `v1alpha` and `v1beta` versions. The Omniverse client library of Kit 110.4 (Isaac Sim 6.2) calls the `v1alpha`
    services, and `v1beta` is served beside them.
-   `nvidia/omniverse/notifications/consumer/v1beta/`, from `notifications/api/protos/`: the event consumer the client
    library uses for the Content window's folder subscriptions.

`../_proto/` holds the Python modules generated from them, so installing the connector needs no code generation. They
were generated with `grpcio-tools` 1.85.4 (protobuf 7.35.1 generated code), which requires the runtime versions the
connector's `requirements-storage.txt` pins. To regenerate after changing the protos or the pins, from this directory:

```bash
python -m grpc_tools.protoc -I . --python_out=../_proto --grpc_python_out=../_proto $(find nvidia -name "*.proto" | sort)
```
