# k3s

Ansible role to install and configure a k3s cluster. Handles master setup, worker setup, firewall configuration, and RBAC user provisioning.

## Tasks

| Task file                          | Description                               |
| ---------------------------------- | ----------------------------------------- |
| `setup_cluster_master.yaml`        | Bootstrap the first master node           |
| `setup_cluster_worker.yaml`        | Join worker nodes to the cluster          |
| `configure_firewall.yaml`          | Open required firewall ports              |
| `configure_dns.yaml`               | Static public node DNS via nmcli (never the in-cluster AdGuard VIP — avoids the image-pull bootstrap deadlock; servers via `k3s_node_dns_servers`, which must name **two different operators**, not two addresses from one) |
| `configure_sysctl.yaml`            | Node kernel tunables via `/etc/sysctl.d/90-k3s.conf` (`k3s_node_sysctls`) — notably `fs.inotify.max_user_instances`, which is per-uid node-wide and stops systemd-in-a-container from booting once exhausted |
| `rpi_configure_boot_cmdline.yaml`  | Raspberry Pi boot config (cgroups)        |
| `configure_rbac.yaml`              | Create scoped RBAC users with kubeconfigs |

## RBAC Users

The `configure_rbac.yaml` task provisions two users using x509 client certificates signed by the cluster CA:

| User     | Group              | ClusterRole        | Cert validity | Access                         |
| -------- | ------------------ | ------------------ | ------------- | ------------------------------ |
| `marck`  | `system:masters`   | `cluster-admin`    | 10 years      | Full cluster admin             |
| `claude` | `deploy-readwrite` | `deploy-readwrite` | 1 year        | Deploy + read/write workloads, Argo CD sync |

The `deploy-readwrite` ClusterRole covers:

- **Workloads**: deployments, statefulsets, daemonsets, replicasets, jobs, cronjobs, HPA — full CRUD
- **Pods**: get/list/watch/create/delete + exec + log + port-forward
- **Networking**: services, endpoints, ingresses — full CRUD
- **Config**: configmaps, secrets — full CRUD
- **Storage**: PersistentVolumeClaims — full CRUD
- **Namespaces**: get/list/watch/create (no delete)
- **ServiceAccounts**: get/list/watch/create/update/patch
- **Nodes & events**: read-only
- **RBAC**: (Cluster)Roles and (Cluster)RoleBindings — full CRUD, bounded by Kubernetes
  escalation prevention (can only grant permissions the role itself holds)
- **Metrics**: metrics.k8s.io nodes/pods — read-only
- **SealedSecrets**: bitnami.com sealedsecrets — full CRUD
- **Certificates**: cert-manager.io certificates — full CRUD
- **Argo CD**: argoproj.io applications, get/list/watch/patch (patch is what triggers a
  sync); appprojects and applicationsets read-only

NOT granted: node management, PersistentVolumes, cluster-level destructive operations,
creating or deleting Argo CD Applications (the app-of-apps repo owns those).

### Variables

Defined in `vars/main.yaml`:

```yaml
k3s_rbac_users:
  - name: marck
    group: system:masters
    cluster_role: cluster-admin
    cert_days: 3650
  - name: claude
    group: deploy-readwrite
    cluster_role: deploy-readwrite
    cert_days: 365

# Destination for kubeconfigs on the Ansible controller
k3s_rbac_kubeconfig_dest: "{{ lookup('env', 'HOME') }}/.kube"

# Certificate storage directory on the master node
k3s_rbac_cert_dir: /etc/rancher/k3s/rbac
```

Override any variable in your playbook `vars:` block or inventory.

### Kubeconfigs

After running the playbook, kubeconfigs are placed on the Ansible controller at:

```text
~/.kube/kubeconfig-marck.yaml
~/.kube/kubeconfig-claude.yaml
```

Use them with:

```bash
export KUBECONFIG=~/.kube/kubeconfig-marck.yaml
kubectl get nodes
```

Or merge into your default kubeconfig:

```bash
KUBECONFIG=~/.kube/config:~/.kube/kubeconfig-marck.yaml kubectl config view --merge --flatten > ~/.kube/config_merged
mv ~/.kube/config_merged ~/.kube/config
```

### Certificate renewal

The `claude` user certificate expires after 1 year. Re-run the playbook after removing the old cert on the master to regenerate:

```bash
# On the master node
sudo rm /etc/rancher/k3s/rbac/claude.crt /etc/rancher/k3s/rbac/claude.csr
# Then re-run the playbook
ansible-playbook install_kubernetes.yaml -i ../../inventories/kubernetes.yaml --tags rbac
```

## Requirements

- k3s installed and cluster running (run `setup_cluster_master.yaml` first)
- `openssl` available on the master node (installed automatically if missing)
- Ansible controller needs write access to `k3s_rbac_kubeconfig_dest`

## k3s version and upgrades

The installer used to be called with no version (`curl -sfL https://get.k3s.io | sh`),
so a node got whatever was "stable" the day it was built. Combined with the install
task being guarded by `when: not k3s_binary.stat.exists`, that meant the role **never
upgraded anything**: the cluster sat on v1.29.6 long after it reached upstream end of
life (2025-02-28).

**A fresh node now follows `k3s_channel` (default `stable`)**, so a new cluster gets the
current release without anyone remembering a version number.

| variable | default | meaning |
| --- | --- | --- |
| `k3s_channel` | `stable` | channel to resolve when no exact version is pinned |
| `k3s_version` | `""` | exact release (`v1.30.14+k3s2`), overrides the channel |
| `k3s_upgrade` | `false` | allow re-running the installer over an existing node |
| `k3s_upgrade_step` | `true` | walk forward one minor per run instead of aiming at `k3s_channel` |
| `k3s_allow_minor_skip` | `false` | permit crossing more than one Kubernetes minor |

### Upgrades are opt-in, and stepwise

Without `k3s_upgrade=true` the role still only installs where k3s is absent, so an
unrelated playbook run can never restart the control plane underneath a running
cluster.

Kubernetes does not support skipping minor versions and k3s inherits that, so an
upgrade crossing more than one minor **fails the play** rather than running. With
`k3s_channel: stable` an out-of-date cluster would otherwise try to jump straight to
the newest release:

```
Refusing to upgrade k3s-mas01 from v1.29.6+k3s2 to v1.36.4+k3s1: that crosses
7 minor versions and Kubernetes supports only one at a time.
```

You do not have to look the next version up. `k3s_upgrade_step` (on by default) targets
**one minor past whatever is installed**, so the same command, run repeatedly, walks the
cluster forward:

```sh
ansible-playbook install_kubernetes.yaml --tags cluster -e k3s_upgrade=true
# -> v1.30.x   verify, then run the identical command again
# -> v1.31.x   ... and so on
# -> once the cluster reaches the stable release it reports "no change"
```

Verify between every hop: nodes Ready on the new version, ArgoCD Applications Healthy,
DNS answering over the LAN path, etcd latency unchanged.

To aim somewhere specific instead, turn stepping off and name the target. The skip guard
still applies:

```sh
ansible-playbook install_kubernetes.yaml --tags cluster \
  -e k3s_upgrade=true -e k3s_upgrade_step=false -e k3s_channel=v1.31
```

Servers are upgraded before agents, which the playbook's separate `masters` and
`workers` plays already give us: the skew policy allows an agent older than its
server, never newer.

Re-running the installer is the documented k3s upgrade path. It replaces the binary
and restarts the unit, so **the API is briefly unavailable on that node**, and on a
single-server cluster that means a short control-plane outage per hop.
