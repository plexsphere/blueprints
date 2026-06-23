<!--
SPDX-FileCopyrightText: Copyright 2026 plexsphere contributors
SPDX-License-Identifier: BUSL-1.1
-->

# plexsphere blueprints

A catalog of [Crossplane](https://www.crossplane.io/) blueprints used by
the plexsphere broker to provision infrastructure across multiple cloud
substrates. Each blueprint is a self-contained bundle of a
`CompositeResourceDefinition` (XRD), a `Composition`, and a metadata
descriptor that the broker consumes to render provider resources.

## What is a blueprint?

A blueprint is one directory under [`catalog/`](catalog/) containing three
files:

| File               | Purpose                                                                                                  |
| ------------------ | -------------------------------------------------------------------------------------------------------- |
| `metadata.json`    | Broker-facing descriptor: slug, display name, supported providers, injection strategy, request schema.   |
| `xrd.yaml`         | The `CompositeResourceDefinition` — the composite API (`blueprints.plexsphere.com`) the blueprint serves. |
| `composition.yaml` | The `Composition` — a single `Pipeline` step that renders the provider managed resource.                 |

When the broker fulfils a request it resolves the request parameters,
stamps cloud tags, and renders the composite defined by the XRD through
its composition.

## Catalog

| Slug                                                              | Display name                 | Provider(s)                  | Injection strategy     | Composite kind            |
| ----------------------------------------------------------------- | ---------------------------- | ---------------------------- | ---------------------- | ------------------------- |
| [`aws-ec2-instance`](catalog/aws-ec2-instance/)                   | AWS EC2 Instance             | aws                          | `provider-secret`      | `XAWSEC2Instance`         |
| [`aws-eks-cluster-daemonset`](catalog/aws-eks-cluster-daemonset/) | AWS EKS Cluster w/ DaemonSet | aws                          | `helm-values`          | `XAWSEKSClusterDaemonSet` |
| [`aws-s3-bucket`](catalog/aws-s3-bucket/)                         | AWS S3 Bucket                | aws                          | `provider-secret`      | `XAWSS3Bucket`            |
| [`hetzner-server`](catalog/hetzner-server/)                       | Hetzner Cloud Server         | hetzner                      | `cloud-init-user-data` | `XHetznerServer`          |
| [`openstack-instance`](catalog/openstack-instance/)               | OpenStack Instance           | openstack                    | `cloud-init-user-data` | `XOpenStackInstance`      |
| [`generic-vm`](catalog/generic-vm/)                               | Generic VM                   | aws, gcp, hetzner, openstack | `cloud-init-user-data` | `XGenericVM`              |
| [`kubernetes-cloudless-node`](catalog/kubernetes-cloudless-node/) | Cloudless Kubernetes Node    | aws                          | `helm-values`          | `XKubernetesCloudlessNode` |

All blueprints are at version `v1alpha1`.

### Blueprint details

**`aws-ec2-instance`** — A single AWS EC2 instance. Request parameters are
threaded in through a provider `Secret`. Renders an Upbound
`ec2.aws.upbound.io/v1beta1` `Instance`.
Request parameters: `region` (required), `instanceType` (required),
`monitoring` (default `false`).

**`aws-eks-cluster-daemonset`** — An AWS EKS cluster with a bundled
DaemonSet. Request parameters are delivered as Helm chart values. Renders
an Upbound `eks.aws.upbound.io/v1beta1` `Cluster`.
Request parameters: `region` (required), `kubernetesVersion` (required),
`nodeCount` (default `3`).

**`aws-s3-bucket`** — A single AWS S3 bucket whose request parameters are
threaded in through a provider `Secret`. It carries no AMI/VPC/subnet
dependency graph, so it is the lowest-risk AWS resource to drive to `Ready`
against a local AWS emulator. The XRD is `Namespaced` (the broker renders
it into the per-Project management-fleet namespace), so the composition
renders a namespaced `s3.aws.m.upbound.io/v1beta1` `Bucket`. Declares
`meshRole: standalone` (see [Mesh role](#mesh-role)) — there is no node to
enrol, so the broker reaches `Ready` the moment the bucket composes.
Request parameters: `region` (required).

**`hetzner-server`** — A single Hetzner Cloud server bootstrapped through
cloud-init user-data. Renders an `hcloud.crossplane.io/v1alpha1` `Server`.
Request parameters: `location` (required), `serverType` (required),
`enableIpv6` (default `true`).

**`openstack-instance`** — A single OpenStack Nova compute instance
bootstrapped through cloud-init user-data. Renders a
`compute.openstack.crossplane.io/v1alpha1` `InstanceV2`.
Request parameters: `availabilityZone` (required), `flavorName`
(required), `rootVolumeGb` (default `50`).

**`generic-vm`** — A provider-agnostic single VM bootstrapped through
cloud-init user-data, targetable at any supported substrate. The base is a
neutral `vm.generic.plexsphere.com/v1alpha1` `VirtualMachine`; the
provider-specific composition is selected at provision time.
Request parameters: `region` (required), `instanceType` (default
`standard-2`), `diskSizeGb` (default `40`).

**`kubernetes-cloudless-node`** — A node materialised entirely in-cluster
through provider-kubernetes with no cloud account. The composition renders
a substrate `ConfigMap` (so the composite reaches `Ready=True`) plus an
enrolment `Job` that POSTs `/v1/register` to register the node against the
control plane, and a `function-auto-ready` step that propagates the
Objects' readiness up to the composite. The XRD is `Namespaced` (the
broker renders it into the per-Project management-fleet namespace), and
the enrolment token arrives through the broker's `helm-values` injection at
`spec.parameters.helmValues.bootstrapToken`. It declares `meshRole: node`
(the default; see [Mesh role](#mesh-role)) — the broker mints a bootstrap
token and waits through `Enrolling` for the node to register. Intended for
the local dev stack and the provisioning tutorial.
Request parameters: none.

## Concepts

### Injection strategies

Each blueprint declares how request parameters reach the rendered
resource:

- **`provider-secret`** — parameters are delivered through a provider
  `Secret`.
- **`helm-values`** — parameters are threaded into the rendered resources
  as Helm chart values.
- **`cloud-init-user-data`** — parameters are injected at first boot via
  cloud-init user-data (the XRD exposes a `userData` field).

Regardless of strategy, every XRD carries a free-form `spec.parameters`
object (`x-kubernetes-preserve-unknown-fields`) that preserves the broker's
request parameters verbatim for the composition to consume. Blueprints that
boot through cloud-init additionally expose a `userData` string, which the
composition copies onto the provider's native user-data field.

### Mesh role

A blueprint may declare an optional `meshRole` in its `metadata.json`,
which the broker reads to decide the resource lifecycle:

- **`node`** (the default when omitted) — the resource provisions a
  mesh-enrolling plexd node. The broker mints a bootstrap token and waits
  through an `Enrolling` phase for the node to register before `Ready`
  (and a `Deregistering` phase on teardown).
- **`standalone`** — the resource is a nodeless cloud object (an S3
  bucket, a managed database) with no node to enrol. The broker mints no
  token and reaches `Ready` the moment the substrate composes, skipping
  `Enrolling` (and `Deregistering`).

`kubernetes-cloudless-node` is a `node` (it renders an enrolment Job that
registers against the control plane); `aws-s3-bucket` is `standalone`, so
the broker can drive it `Pending → Provisioning → Ready` against a local
AWS emulator with no node to wait on. The remaining blueprints omit the
field and inherit the `node` default.

### Cloud tags

Every XRD exposes a `cloudTags` map. These are propagation-resolved
cloud-provider tags stamped by the broker at render time (with a
`plexsphere:` prefix). Each provider-backed composition copies
`spec.cloudTags` onto the provider's native tagging field, which differs
per provider:

| Provider  | Provider tag field          |
| --------- | --------------------------- |
| AWS       | `spec.forProvider.tags`     |
| Hetzner   | `spec.forProvider.labels`   |
| OpenStack | `spec.forProvider.metadata` |
| Generic   | `spec.forProvider.tags` \*  |

\* The generic blueprint targets the conservative `spec.forProvider.tags`
field, which the provider-specific composition rewrites to its own
dialect.

The `kubernetes-cloudless-node` blueprint is the exception: it renders
entirely in-cluster through provider-kubernetes and has no cloud tagging
field to map onto, so its composition does not patch `spec.cloudTags`.

## Conventions

- **API group:** all composite resources live under
  `blueprints.plexsphere.com`.
- **XRD API version:** `apiextensions.crossplane.io/v2`.
- **Composition API version:** `apiextensions.crossplane.io/v1`, run in
  `Pipeline` mode with a single `function-patch-and-transform` step.
- **Composition name** matches the blueprint slug.
- Every YAML file carries an SPDX header. The compositions document their
  base-resource choices inline as `DECISION:` notes.

## Bundle contract

The catalog is published as a single **plain OCI artifact** (built with
[`oras`](https://oras.land)) that the plexsphere `BlueprintSource` pulls
and reconciles at runtime. The bundle carries one layer per catalog
file — including `metadata.json`, so the consumer reads the catalog
attributes from the bundle, not just the raw Crossplane documents.

- **Artifact type:** `application/vnd.plexsphere.blueprints.catalog.v1`
- **One layer per file**, each tagged with its repo-relative path in the
  `org.opencontainers.image.title` annotation
  (`catalog/<slug>/<file>`) and a per-kind media type:

  | File               | Layer media type                                          |
  | ------------------ | --------------------------------------------------------- |
  | `xrd.yaml`         | `application/vnd.plexsphere.blueprint.xrd.v1+yaml`         |
  | `composition.yaml` | `application/vnd.plexsphere.blueprint.composition.v1+yaml` |
  | `metadata.json`    | `application/vnd.plexsphere.blueprint.metadata.v1+json`    |

The consumer walks the layers, groups them by slug directory from the
title annotation, and ingests the three files per blueprint through its
existing publish path. The build is implemented in
[`scripts/build-bundle.sh`](scripts/build-bundle.sh).

## Versioning & releases

Bundles are versioned with semver, mapped 1:1 to immutable OCI tags
(`v0.1.0`, `v0.2.0`, …); the tag is what plexsphere pins and pulls. The
`metadata.json` `version` field tracks the per-blueprint API version
(`v1alpha1`) and is independent of the bundle tag.

Pushing a `v*` git tag triggers [`.github/workflows/release.yml`](.github/workflows/release.yml),
which validates, builds, pushes the bundle to
`ghcr.io/plexsphere/blueprints:<tag>`, and signs it.

```sh
git tag v0.1.0 && git push origin v0.1.0
```

## Verifying the bundle

Each published bundle is signed keyless with
[cosign](https://github.com/sigstore/cosign) (Sigstore: a Fulcio
code-signing certificate plus a Rekor inclusion proof), mirroring the
verification path the plexsphere Artifact Registry context uses. The
signature is attached in the self-contained Sigstore bundle format
(`application/vnd.dev.sigstore.bundle.v0.3+json`), so verification needs
`--new-bundle-format` and cosign `>= 2.6`. Verify a pulled bundle against
the release workflow's signing identity:

```sh
cosign verify ghcr.io/plexsphere/blueprints:v0.1.0 \
  --new-bundle-format \
  --certificate-identity-regexp '^https://github.com/plexsphere/blueprints/.github/workflows/release.yml@.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

## Local development

All checks the CI gate runs are reproducible locally:

```sh
python3 scripts/validate.py                  # structural checks (manifest.ValidatePair mirror)
yamllint -c .yamllint.yml catalog/ examples/ # lint
scripts/build-bundle.sh --dry-run ghcr.io/plexsphere/blueprints:dev  # assemble into ./_bundle, no push
crossplane render examples/<slug>/xr.yaml catalog/<slug>/composition.yaml examples/functions.yaml
```

## Adding a blueprint

1. Create `catalog/<slug>/` with `xrd.yaml`, `composition.yaml`, and
   `metadata.json`, following the [Conventions](#conventions) above and
   carrying the SPDX header on each YAML file.
2. Add an `examples/<slug>/xr.yaml` composite so CI can `crossplane
   render` it.
3. Run `python3 scripts/validate.py` and the lint/render steps above
   until green.
4. Open a PR. The `validate` workflow gates lint, structural validation,
   the build dry-run, and render. Once merged, cut a new `v*` tag to
   publish a signed bundle.

## License

Licensed under the Business Source License 1.1 (`BUSL-1.1`).
Copyright 2026 plexsphere contributors.
