#!/usr/bin/env python3.10
import argparse
import json
from itertools import islice
import boto3
import sys
import time

REGION: str = "us-east-2"
SRC_REPO = "https://github.com/conelul/disposables"
NUM_TRIALS = 3
NUM_TABS = 20
LOAD_TIME_MS = 15000
DB_URI = "mongodb://root:pass1234@disposablesdb.c39d9rmhj8ez.us-east-2.docdb.amazonaws.com:27017/?retryWrites=false"


def splita(a: list, n: int):
    k, m = divmod(len(a), n)
    return (a[i * k + min(i, m) : (i + 1) * k + min(i + 1, m)] for i in range(n))


def split(d: dict, n: int):
    l = []
    for (k, a) in d.items():
        for v in a:
            l.append((k, v))
    res = []
    for ch in splita(l, n):
        d = {}
        for (k, v) in ch:
            d.setdefault(k, []).append(v)
        res.append(d)
    return res


def main() -> None:
    #! Args
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-n",
        "--num-servers",
        help="Number of servers to shard collection over",
        default=1,
    )
    parser.add_argument("input")
    args = parser.parse_args()
    #! Load input
    with open(args.input) as f:
        sites = json.load(f)
        chunks = split(sites, int(args.num_servers))
    #! Deploy
    ssm = boto3.client("ssm", region_name=REGION)
    ec2 = boto3.client("ec2", region_name=REGION)
    waiter = ec2.get_waiter("instance_status_ok")
    # Config to install SSM
    init = f"""#cloud-config
repo_update: true
repo_upgrade: all

runcmd:
    - sudo amazon-linux-extras install epel -y
    - sudo yum install -y https://s3.amazonaws.com/ec2-downloads-windows/SSMAgent/latest/linux_amd64/amazon-ssm-agent.rpm
    - sudo yum install -y git chromium gcc
    - cd /home/ec2-user/
    - wget https://github.com/conelul/disposables/releases/download/musl/collection
    - sudo chmod 777 ./collection
    
"""
    # Create servers
    servers = ec2.run_instances(
        LaunchTemplate={"LaunchTemplateId": "lt-02c15b50d3a70c40f"},
        UserData=init,
        MinCount=int(args.num_servers),
        MaxCount=int(args.num_servers),
    )
    instance_ids = [instance["InstanceId"] for instance in servers["Instances"]]
    print(f"instance ids: {instance_ids}")
    print("waiting for server init")
    waiter.wait(InstanceIds=instance_ids)
    print("waiting three minutes for cloud-config")
    time.sleep(2.5 * 60)
    print("finished waiting")
    for (instance, inp) in zip(servers["Instances"], chunks):
        id = instance["InstanceId"]
        cmds = [
            f"echo '{json.dumps(inp)}' > /home/ec2-user/input.json",
            f"cd /home/ec2-user && for ((i = 1 ; i <= {NUM_TRIALS} ; i++ )); do RUST_LOG=info ./collection --trial $i --server-id {id} --tabs {NUM_TABS} --load-time-ms {LOAD_TIME_MS} --input input.json --db-uri {DB_URI} --db-name disposables-proof --coll-name sites50 > $i.log 2>&1; done",
        ]
        resp = ssm.send_command(
            InstanceIds=[id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": cmds},
        )


if __name__ == "__main__":
    main()
