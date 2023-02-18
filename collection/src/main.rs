#![feature(let_chains, arc_into_inner)]

extern crate pretty_env_logger;
#[macro_use]
extern crate log;

use std::{borrow::Borrow, fs::OpenOptions, io::BufReader, sync::Arc, thread, time::Duration};

use anyhow::{anyhow, Result};
use chromiumoxide::{
    browser::{Browser, BrowserConfig},
    cdp::browser_protocol::{
        browser::GetVersionReturns,
        fetch::{ContinueRequestParams, EventRequestPaused},
        network::{EventResponseReceived, ResourceType},
    },
};
use chrono::{NaiveDateTime, Utc};
use clap::Parser;
use crossbeam::channel;
use futures::{stream, StreamExt};
use hashbrown::HashMap as Map;
use lazy_static::lazy_static;
use mongodb::{
    bson::{self, doc},
    options::UpdateOptions,
    Client, Collection,
};
use serde::{Deserialize, Serialize};
use tokio::{sync::Mutex, task::JoinHandle, time::sleep};
use trust_dns_resolver::{
    config::*,
    proto::rr::{Record, RecordType},
    TokioAsyncResolver,
};
use url::Url;
use mongodb::bson::bson;
#[derive(Parser, Debug)]
struct Args {
    #[arg(long)]
    trial: i32,
    #[arg(long)]
    tabs: usize,
    #[arg(short, long)]
    load_time_ms: u64,
    #[arg(long)]
    input: String,
    #[arg(long)]
    db_uri: String,
    #[arg(long)]
    db_name: String,
    #[arg(long)]
    coll_name: String,
}

#[derive(Serialize, Deserialize)]
struct Input(Map<String, Vec<String>>);
impl Input {
    fn from_file(path: &str) -> Result<Self> {
        let rdr = BufReader::new(OpenOptions::new().read(true).open(path)?);
        Ok(serde_json::from_reader(rdr)?)
    }
}

#[derive(Serialize, Deserialize)]
struct Site {
    hostname: String,
    category: String,
    trials: Vec<Trial>,
}

#[derive(Serialize, Deserialize)]
struct Trial {
    time: NaiveDateTime,
    ver: GetVersionReturns,
    num: i32,
    resources: Vec<Resource>,
    trial_success: bool,
}
#[derive(Serialize, Deserialize)]
struct Resource {
    url: String,
    hostname: Option<String>,
    resource_type: ResourceType,

    code: Option<i64>,
    size: Option<f64>,
    dns_records: Option<Vec<Record>>,
}

const DNS_CACHE_SIZE: usize = 256;

#[tokio::main]
async fn main() -> Result<()> {
    pretty_env_logger::init();
    info!("init logging");

    lazy_static! {
        static ref ARGS: Args = Args::parse();
    }
    info!("got args");

    let input = Input::from_file(&ARGS.input)?;
    let coll: Collection<Trial> = Client::with_uri_str(&ARGS.db_uri)
        .await?
        .database(&ARGS.db_name)
        .collection(&ARGS.coll_name);
    info!("connected to MongoDB collection: `{}`", coll.name());

    let mut resolv_conf = ResolverOpts::default();
    resolv_conf.cache_size = DNS_CACHE_SIZE;
    let resolver = TokioAsyncResolver::tokio(ResolverConfig::cloudflare(), resolv_conf)?;
    info!("init DNS resolver");

    let (raw_browser, mut handler) = Browser::launch(
        BrowserConfig::builder()
            .with_head()
            .enable_request_intercept()
            .incognito()
            .enable_cache()
            .build()
            .map_err(|s| anyhow!(s))?,
    )
    .await?;
    info!("started browser");

    let b = Arc::new(raw_browser);
    // spawn a new task that continuously polls the handler
    let handle = tokio::spawn(async move {
        loop {
            let _ = handler.next().await.unwrap();
        }
    });
    info!("started websocket handler");
    let v = b.version().await?;

    let buffer_time = Duration::from_millis(ARGS.load_time_ms).div_f32(ARGS.tabs as f32);
    debug!("buffer_time: {:?}", buffer_time);

    for (category, sites) in input.0 {
        let category = Arc::new(category);
        let mut trials = stream::iter(sites.into_iter().enumerate().map(
            |(i, site)| -> JoinHandle<Result<_>> {
                if i < ARGS.tabs {
                    thread::sleep(buffer_time);
                }
                let b = b.clone();
                let site = Arc::new(site);
                let category = category.clone();
                let resolver = resolver.clone();
                let v = v.clone();
                let coll = coll.clone();

                tokio::spawn(async move {
                    let page = b.new_page("about:blank").await?;
                    let intercept_page = page.clone();
                    debug!("{}: opened new page", site);
                    info!("{}: starting collection", site);

                    let site2 = site.clone();
                    let mut paused_reqs = page.event_listener::<EventRequestPaused>().await?;
                    let (reqs, reqr) = channel::unbounded();
                    let req_handle = tokio::spawn(async move {
                        while let Some(req) = paused_reqs.next().await {
                            if let Err(e) = intercept_page
                                .execute(ContinueRequestParams::new(req.request_id.clone()))
                                .await
                            {
                                warn!(
                                    "{}: failed to continue request for {}: {}",
                                    site2, req.request.url, e
                                );
                            }
                            if let Err(e) = reqs.send(req.clone()) {
                                warn!(
                                    "{}: failed to send info for {}: {}",
                                    site2, req.request.url, e
                                );
                            } else {
                                debug!("{} -> {}: {:?}", site2, req.request.url, req.network_id);
                            };
                        }
                    });

                    let rmap = Arc::new(Mutex::new(Map::new()));

                    let mut resps = page.event_listener::<EventResponseReceived>().await?;
                    let site2 = site.clone();
                    let rmap2 = rmap.clone();
                    let resp_handle = tokio::spawn(async move {
                        while let Some(resp) = resps.next().await {
                            if rmap2
                                .lock()
                                .await
                                .insert(resp.request_id.inner().clone(), resp.clone())
                                .is_some()
                            {
                                warn!(
                                    "{}: duplicate response ids for {}",
                                    site2, resp.response.url
                                );
                            }
                            debug!(
                                "{} <- {}: {}",
                                site2,
                                resp.response.url,
                                resp.request_id.inner()
                            );
                        }
                    });

                    let site2 = site.clone();
                    let page2 = page.clone();
                    let load_handle: JoinHandle<Result<_>> = tokio::spawn(async move {
                        page2
                            .goto(format!("https://{}", site2))
                            .await
                            .map_err(|e| anyhow!(e))?;
                        Ok(())
                    });

                    sleep(Duration::from_millis(ARGS.load_time_ms)).await;
                    load_handle.abort();
                    info!("{}: stopped loading", site);
                    debug!("{}: got {} requests", site, reqr.len());

                    req_handle.abort();
                    resp_handle.abort();
                    debug!("{}: detached request/response handlers", site);

                    let mut buf = stream::iter(reqr.into_iter().map(|req| {
                        let site = site.clone();
                        let resolver = resolver.clone();
                        let rmap = rmap.clone();
                        tokio::spawn(async move {
                            let hostname = Url::parse(&req.request.url)
                                .ok()
                                .and_then(|u| u.host_str().and_then(|h| Some(h.to_string())));
                            let dns_records = if let Some(hostname) = hostname.borrow() {
                                match resolver.lookup(hostname, RecordType::A).await {
                                    Ok(i) => {
                                        debug!("{}: got DNS info for {}", site, req.request.url);
                                        Some(i.records().to_vec())
                                    }
                                    Err(e) => {
                                        warn!(
                                            "{}: failed to lookup DNS info for {}: {}",
                                            site, hostname, e
                                        );
                                        None
                                    }
                                }
                            } else {
                                warn!("no hostname in {}", &req.request.url);
                                None
                            };

                            let (code, size) = if let Some(id) = &req.network_id {
                                if let Some(resp) = rmap.lock().await.get(id.inner()) {
                                    (
                                        Some(resp.response.status),
                                        Some(resp.response.encoded_data_length),
                                    )
                                } else {
                                    warn!("{}: no response for {}", site, req.request.url);
                                    (None, None)
                                }
                            } else {
                                warn!("{}: no network id for {}", site, req.request.url);
                                (None, None)
                            };

                            let url = req.request.url.clone();
                            let resource_type = req.resource_type.clone();

                            Resource {
                                url,
                                hostname,
                                resource_type,
                                code,
                                size,
                                dns_records,
                            }
                        })
                    }))
                    .buffer_unordered(ARGS.tabs);

                    let mut resources = Vec::new();
                    while let Some(res) = buf.next().await {
                        match res {
                            Ok(r) => {
                                resources.push(r);
                            }
                            Err(e) => {
                                warn!("{}: failed to read response: {}", site, e);
                            }
                        }
                    }
                    info!("{}: got {} resources", site, resources.len());

                    let trial = Trial {
                        time: Utc::now().naive_local(),
                        ver: v,
                        num: ARGS.trial,
                        resources,
                        trial_success: false,
                    };

                    let opts = UpdateOptions::builder().upsert(true).build();
                    let site: &String = site.borrow();
                    let category: &String = category.borrow();

                    let res = coll
                        .update_one(
                            doc! {"hostname": site, "category": category},
                            doc! {
                                    "$push": { "trials" : bson::to_bson(&trial)?    }
                            },
                            opts,
                        )
                        .await?;
                    if res.upserted_id.is_some() {
                        debug!("{}: upserted trial {}", site, res.upserted_id.unwrap());
                    }
                    info!("{}: inserted new trial", site);

                    page.close().await?;
                    debug!("{}: closed page", &site);
                    Ok(())
                })
            },
        ))
        .buffer_unordered(ARGS.tabs);
        while let Some(trial) = trials.next().await {
            match trial {
                Ok(Ok(_)) => {}
                Ok(Err(e)) => error!("`{}`: trial error: {}", &category, e),
                Err(e) => error!("`{}`: join error: {}", &category, e),
            }
        }
    }
    coll.update_many(
        doc! { "trials.num": &ARGS.trial },
        doc! {"$set" : {"trials.$.trial_success" : true} },
        None,
    )
    .await?;
    Arc::into_inner(b)
        .ok_or(anyhow!("browser still in use when closed"))?
        .close()
        .await?;
    info!("closed browser");
    handle.await?;
    Ok(())
}
