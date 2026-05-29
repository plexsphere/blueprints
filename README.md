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
| [`hetzner-server`](catalog/hetzner-server/)                       | Hetzner Cloud Server         | hetzner                      | `cloud-init-user-data` | `XHetznerServer`          |
| [`openstack-instance`](catalog/openstack-instance/)               | OpenStack Instance           | openstack                    | `cloud-init-user-data` | `XOpenStackInstance`      |
| [`generic-vm`](catalog/generic-vm/)                               | Generic VM                   | aws, gcp, hetzner, openstack | `cloud-init-user-data` | `XGenericVM`              |

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

### Cloud tags

Every XRD exposes a `cloudTags` map. These are propagation-resolved
cloud-provider tags stamped by the broker at render time (with a
`plexsphere:` prefix). Each composition copies `spec.cloudTags` onto the
provider's native tagging field, which differs per provider:

| Provider  | Provider tag field          |
| --------- | --------------------------- |
| AWS       | `spec.forProvider.tags`     |
| Hetzner   | `spec.forProvider.labels`   |
| OpenStack | `spec.forProvider.metadata` |
| Generic   | `spec.forProvider.tags` \*  |

\* The generic blueprint targets the conservative `spec.forProvider.tags`
field, which the provider-specific composition rewrites to its own
dialect.

## Conventions

- **API group:** all composite resources live under
  `blueprints.plexsphere.com`.
- **XRD API version:** `apiextensions.crossplane.io/v2`.
- **Composition API version:** `apiextensions.crossplane.io/v1`, run in
  `Pipeline` mode with a single `function-patch-and-transform` step.
- **Composition name** matches the blueprint slug.
- Every YAML file carries an SPDX header. The compositions document their
  base-resource choices inline as `DECISION:` notes.

## License

Licensed under the Business Source License 1.1 (`BUSL-1.1`).
Copyright 2026 plexsphere contributors.
