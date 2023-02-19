MONGO_URI = "mongodb://root:PASSWORD@disposablesdb.c39d9rmhj8ez.us-east-2.docdb.amazonaws.com:27017/?retryWrites=false"
DB_NAME = "disposables-proof"
DB_COLL = "sites50"
TRIAL_NUM = 1

import itertools
from pandas.io.json import json_normalize
import json
import pandas as pd
from pymongo import MongoClient

conn = MongoClient(MONGO_URI)
db = conn[DB_NAME]
input_data = db[DB_COLL]

resources = []
for site in input_data.find(
    {"trials.num": TRIAL_NUM},
    {
        "_id": 0,
        "hostname": 1,
        "category": 1,
        "trials": {"$elemMatch": {"num": TRIAL_NUM}},
    },
):
    origin = site["hostname"]
    category = site["category"]
    trial = site["trials"][0]
    trial_num = trial["num"]
    for resource in trial["resources"]:
        dns = resource["dns_records"]
        if dns is not None and len(dns) > 0:
            first_ttl = dns[0]
        else:
            first_ttl = None
        resources.append(
            (
                origin,
                trial_num,
                resource["url"],
                resource["hostname"],
                category,
                resource["resource_type"],
                resource["code"],
                resource["size"],
                first_ttl,
                resource["dns_records"],
            )
        )

df = pd.DataFrame(
    resources,
    columns=[
        "origin",
        "trial_num",
        "url",
        "hostname",
        "category",
        "resource_type",
        "code",
        "size",
        "first_ttl",
        "dns_records",
    ],
)
df = df.drop(columns=["dns_records"])

trial_1 = df[df["trial_num"] == TRIAL_NUM]
trial_1 = trial_1.drop_duplicates(subset=["origin", "hostname"])
hn_list = trial_1["hostname"].str.split(".").to_list()
# x
sld_list = []
for i in hn_list:
    try:
        sld_list.append(i[-2])
    except:
        sld_list.append("none")
trial_1 = trial_1.assign(SLD=sld_list)

trial_1["real_hostname"] = trial_1["hostname"].str.split(".", expand=True)[0]


trial_1["real_hostname_length"] = trial_1["real_hostname"].str.len()

trial_1.to_csv(f"{DB_COLL}.csv", index=False)
