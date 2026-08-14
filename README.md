# k3s

Ansible role to install and configure a k3s cluster. Handles master setup, worker setup, firewall configuration, and RBAC user provisioning.

## Tasks

| Task file                          | Description                               |
| ---------------------------------- | ----------------------------------------- |
| `setup_cluster_master.yaml`        | Bootstrap the first master node           |
| `setup_cluster_worker.yaml`        | Join worker nodes to the cluster          |
| `configure_firewall.yaml`          | Open required firewall ports              |
| `configure_dns.yaml`               | Static public node DNS via nmcli (never the in-cluster AdGuard VIP — avoids the image-pull bootstrap deadlock; servers via `k3s_node_dns_servers`) |
| `configure_sysctl.yaml`            | Node kernel tunables via `/etc/sysctl.d/90-k3s.conf` (`k3s_node_sysctls`) — notably `fs.inotify.max_user_instances`, which is per-uid node-wide and stops systemd-in-a-container from booting once exhausted |
| `rpi_configure_boot_cmdline.yaml`  | Raspberry Pi boot config (cgroups)        |
| `configure_rbac.yaml`              | Create scoped RBAC users with kubeconfigs |

## RBAC Users

The `configure_rbac.yaml` task provisions two users using x509 client certificates signed by the cluster CA:

| User     | Group              | ClusterRole        | Cert validity | Access                         |
| -------- | ------------------ | ------------------ | ------------- | ------------------------------ |
| `marck`  | `system:masters`   | `cluster-admin`    | 10 years      | Full cluster admin             |
| `claude` | `deploy-readwrite` | `deploy-readwrite` | 1 year        | Deploy + read/write workloads  |

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

NOT granted: node management, PersistentVolumes, cluster-level destructive operations.

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
