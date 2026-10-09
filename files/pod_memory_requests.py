"""Print, in MiB rounded up, the memory the kubelet admits a node's pods by.

Reads a PodList (kubectl get pods -o json) on stdin. Per pod: its containers
plus restartable init containers (sidecars), or its largest regular init
container plus those sidecars if that is larger, plus pod overhead. Slightly
conservative for sidecars that start after a regular init container.
"""
import json
import math
import re
import sys

UNITS = {
    "": 1, "m": 1e-3, "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12,
    "Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40,
}


def quantity(value):
    match = re.fullmatch(r"([0-9.]+(?:[eE][-+]?[0-9]+)?)([A-Za-z]*)", str(value))
    if not match or match.group(2) not in UNITS:
        sys.exit(f"cannot parse memory quantity {value!r}")
    return float(match.group(1)) * UNITS[match.group(2)]


def request(container):
    requests = (container.get("resources") or {}).get("requests") or {}
    return quantity(requests.get("memory", "0"))


total = 0.0
for pod in json.load(sys.stdin)["items"]:
    spec = pod["spec"]
    inits = spec.get("initContainers") or []
    sidecars = sum(request(c) for c in inits if c.get("restartPolicy") == "Always")
    regular = max([request(c) for c in inits if c.get("restartPolicy") != "Always"] or [0])
    running = sum(request(c) for c in spec["containers"]) + sidecars
    total += max(running, regular + sidecars)
    total += quantity((spec.get("overhead") or {}).get("memory", "0"))
print(math.ceil(total / 2**20))
