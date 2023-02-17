{pkgs ? import <nixpkgs> {}}:
pkgs.mkShell {
  packages = with pkgs; [
    python3Packages.boto3
    python3Packages.mypy-boto3-s3
    python3Packages.mypy-boto3-builder
    argparse
  ];
}
