#!/usr/bin/env python3.10
import argparse
import json
from itertools import islice
import boto3

REGION: str = "us-east-2"
SRC_REPO = "https://github.com/conelul/disposables"


def chunks(data: dict[str, list[str]], size):
    it = iter(data)
    for i in range(0, len(data), size):
        yield {k: data[k] for k in islice(it, size)}


# def run_server(client, input: dict[str, list[str]]) -> dict:


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
        input_name = f.name
    #! Deploy
    ssm = boto3.client("ssm", region_name=REGION)
    ec2 = boto3.client("ec2", region_name=REGION)
    # Config to install SSM
    init = """#cloud-config
repo_update: true
repo_upgrade: all

runcmd:
    - sudo amazon-linux-extras install epel -y
    - sudo yum install -y https://s3.amazonaws.com/ec2-downloads-windows/SSMAgent/latest/linux_arm64/amazon-ssm-agent.rpm
    - sudo yum install -y git
    - sudo yum install -y chromium
    - sudo yum install -y golang
"""
    # Create servers
    servers = ec2.run_instances(
        LaunchTemplate={"LaunchTemplateId": "lt-02c15b50d3a70c40f"},
        UserData=init,
        # SecurityGroupIds=["sg-0816ba1f92424b2f6"],
        MinCount=args.num_servers,
        MaxCount=args.num_servers,
    )
    for (instance, inp) in zip(servers["Instances"], chunks(sites, args.num_servers)):
        cmds = [f'echo {str(inp)} > input.json', f'git clone {SRC_REPO} -o src', f'cd src/collection && go mod tidy && go run main.go > log.txt']
        resp = ssm.send_command(
            InstanceIds=[instance["InstanceId"]],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": cmds},
        )


# Clean input - run over 10 servers (~2k each) and then meet

if __name__ == "__main__":
    main()
