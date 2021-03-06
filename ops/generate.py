#!/usr/bin/env pypy
from __future__ import print_function

import json
import os
import re
import sys

stdout = sys.stdout
sys.stdout = sys.stderr

# We use pypy so that dictionary order is preserved.
# pypy2 preserves dictionary order everywhere! Even for kwargs!
# A convenient docker container is provided if you just run 'make'.
assert "pypy" in sys.executable, "DO NOT PROCEED."

# CoreOS version 647.0.0
COREOS_AMI = "ami-4b1c763c"


def ref(x):
    return {'Ref': x}


def join(*args, **kwargs):
    """
    Return an intrinsic joining each argument together with an empty seperator.
    join("foo", bar) is shorthand for:
    {"Fn::Join": ["", list(("foo", bar))]}
    """
    sep = kwargs.pop("sep", "")
    assert not kwargs, "unknown params specified: {}".format(kwargs.keys())
    return {"Fn::Join": [sep, list(args)]}


def make_tags(**kwargs):
    """
    For each kwarg, produce a {"Key": key, "Value": value}.
    """
    return [{"Key": key, "Value": value} for key, value in kwargs.items()]


def inject_refs(template, **params):
    """
    Template in refs using Fn::Join (join)

    inject_refs("foo {{bar}} baz", bar=ref("hello")
      -> join("foo ", ref("hello"), " baz")
    """

    def substitute(part):
        match = re.match("^{{(.*)}}$", part)
        if not match:
            return part
        (param_name,) = match.groups()
        return params[param_name]

    parts = re.split("({{.*?}})", template)
    parts = [substitute(part) for part in parts]

    if len(parts) == 1:
        # Nothing to join
        (part,) = parts
        return part

    return join(*parts)


def load_user_data(filename, **kwargs):
    """
    Load `filename`, template in `kwargs` dynamically (kwarg values may be
    cloud formation json values).
    """

    with open(filename) as fd:
        content = fd.read()

    lines = content.split("\n")
    # Template in parts matching {{foo}} with kwargs
    lines = [inject_refs(line, **kwargs) for line in lines]

    return {"Fn::Base64": join(sep="\n", *lines)}


main = {
    "AWSTemplateFormatVersion": "2010-09-09",

    "Description": "CloudFormation for mot.scraperwiki.com",

    "Parameters": {

        "InstanceType": {
            "Description": "WebServer EC2 instance type",
            "Type": "String",
            "Default": "t2.medium",
        },

        "HookbotMonitorUrl": {
            "Description": "Hookbot Monitor URL",
            "Type": "String",
        },

        "VpcId": {
            "Description": "",
            "Type": "AWS::EC2::VPC::Id",
        },

        "SubnetId": {
            "Description": "",
            "Type": "AWS::EC2::Subnet::Id",
        },

        "HostedZoneName": {
            "Default": "scraperwiki.com",
            "Description": "Top level domain to use.",
            "Type": "String",
        },
    },

    "Resources": {

        "MOTRole": {
            "Type": "AWS::IAM::Role",
            "Properties": {
                "AssumeRolePolicyDocument": {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": ["ec2.amazonaws.com"]},
                            "Action": ["sts:AssumeRole"],
                        },
                    ],
                },
                "Path": "/mot/",
            },
        },

        "MOTInstanceProfile": {
            "Type": "AWS::IAM::InstanceProfile",
            "Properties": {
                "Path": "/mot/",
                "Roles": [ref("MOTRole")],
            }
        },

        "AccessConfigurationBucket": {
            "Type": "AWS::IAM::Policy",
            "Properties": {
                "PolicyName": "can-access-ssl-certificates",
                "PolicyDocument": {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": [
                                "s3:GetObject",
                            ],
                            "Resource": [
                                # Private SSL key and certificate
                                "arn:aws:s3:::scraperwiki-keys/ssl/*",
                            ],
                        },
                    ],
                },
                "Roles": [ref("MOTRole")],
            },
        },

        "MOTSecurityGroup": {
            "Type": "AWS::EC2::SecurityGroup",
            "Properties": {
                "VpcId": ref('VpcId'),
                "Tags": make_tags(Name='MOT', Project='mot'),
                "GroupDescription": "Enable SSH, HTTP and HTTPS from everywhere",
                "SecurityGroupIngress": [
                    {"IpProtocol": "tcp", "FromPort": "22",
                     "ToPort": "22", "CidrIp": "0.0.0.0/0"},
                    {"IpProtocol": "tcp", "FromPort": "80",
                     "ToPort": "80", "CidrIp": "0.0.0.0/0"},
                    {"IpProtocol": "tcp", "FromPort": "443",
                     "ToPort": "443", "CidrIp": "0.0.0.0/0"},
                ],
            },
        },

        "MOTInstance": {
            "Type": "AWS::EC2::Instance",
            "Properties": {
                "Tags": make_tags(Name='MOT', Project='mot'),
                "ImageId": COREOS_AMI,
                "SubnetId": ref('SubnetId'),
                "InstanceType": {"Ref": "InstanceType"},
                "IamInstanceProfile": ref("MOTInstanceProfile"),
                "SecurityGroupIds": [{"Ref": "MOTSecurityGroup"}],
                "UserData": load_user_data(
                    'mot-user-data.yml',
                    hookbot_monitor_url=ref('HookbotMonitorUrl'),
                ),
            },
        },

        "MOTDNS": {
            "Type": "AWS::Route53::RecordSet",
            "Properties": {
                "HostedZoneName": join(ref("HostedZoneName"), "."),
                "Comment": "DNS name for mot.scraperwiki.com",

                # e.g, mot-20150413-pw-dev-eu-west-1.scraperwiki.com
                "Name": join(
                    join(
                        ref("AWS::StackName"),
                        ref("AWS::Region"),
                        sep="-",
                    ),
                    ref("HostedZoneName"),
                    "",  # Must end with a ".", so join empty.
                    sep="."
                ),
                "Type": "A",
                "TTL": "60",
                "ResourceRecords": [
                    {'Fn::GetAtt': ['MOTInstance', 'PublicIp']},
                ],
            },
        },
    },

    "Outputs": {
        "MOTDNS": {
            "Value": ref("MOTDNS"),
            "Description": "DNS name of MOT instance"
        }
    }
}

json.dump(main, stdout, indent=2)
stdout.write('\n')
