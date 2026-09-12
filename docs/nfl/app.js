"use strict";
const $ = (s) => document.querySelector(s);
const fmtOdds = (n) => n == null ? "—" : (n > 0 ? `+${n}` : String(n));
const pct = (n) => n == null ? "—" : `${(Number(n) * 100).toFixed(1)}%`;
const num = (n, d=1) => n == null ? "—" : Number(n).toFixed(d);
const esc = (s) => { const d=document.createElement("div"); d.textContent=s ?? ""; return d.innerHTML; };
const CT = new Intl.DateTimeFormat("en-US", {timeZone:"America/Chicago",weekday:"short",hour:"numeric",minute:"2-digit",timeZoneName:"short"});
const LOCAL = new Intl.DateTimeFormat(undefined,{hour:"numeric",minute:"2-digit"});

function ageText(ts){
  const ms=Date.now()-Date.parse(ts);
  if(!Number.isFinite(ms)) return "time unknown";
  const m=Math.max(0,Math.floor(ms/60000));
  return m<1?"just now":m===1?"1 minute ago":`${m} minutes ago`;
}
function statusLabel(r){return r.decision_status==="SHADOW_ONLY"?"SHADOW ELIGIBLE":"QUARANTINED";}
function directionClass(d){return d==="OVER"?"lean-over":d==="UNDER"?"lean-under":"lean-neutral";}
function gates(r){
  const out=['<span class="gate">Identity: exact GSIS</span>'];
  if(r.role_continuity_status) out.push(`<span class="gate${r.role_continuity_status.includes("UNCERTAINTY")?" warn":""}">${esc(r.role_continuity_status.replaceAll("_"," "))}</span>`);
  if(r.availability_status) out.push(`<span class="gate${r.availability_status.startsWith("UNKNOWN")||r.availability_status.includes("INACTIVE")?" warn":""}">${esc(r.availability_status.replaceAll("_"," "))}</span>`);
  for(const reason of r.quarantine_reasons||[]) out.push(`<span class="gate warn">${esc(reason.replaceAll("_"," "))}</span>`);
  return out.join("");
}
function playerCard(r){
  const badge=r.decision_status==="SHADOW_ONLY"?"shadow":"quarantine";
  return `<article class="player">
    <div class="player-top"><div><h2>${esc(r.player_name)}</h2><div class="team">${esc(r.team)} vs ${esc(r.opponent)}</div></div><span class="badge ${badge}">${statusLabel(r)}</span></div>
    <div class="market-line"><span class="line">${num(r.line)}</span><span class="label">passing yards</span></div>
    <div class="price-row"><div class="stat"><span>Over</span><b>${fmtOdds(r.over_odds)}</b></div><div class="stat"><span>Under</span><b>${fmtOdds(r.under_odds)}</b></div><div class="stat"><span>Market O</span><b>${pct(r.market_fair_over_probability)}</b></div></div>
    <div class="model-row"><div class="stat"><span>FC projection</span><b>${num(r.model_projection)}</b></div><div class="stat"><span>Model lean</span><b class="${directionClass(r.research_direction)}">${esc(r.research_direction||"—")}</b></div><div class="stat"><span>Research edge</span><b>${r.research_edge==null?"—":`${(r.research_edge*100).toFixed(1)} pts`}</b></div></div>
    <div class="gate-list">${gates(r)}</div>
    <div class="captured">Market observed ${ageText(r.captured_at)} · ${esc(r.source)}</div>
  </article>`;
}
function gameBlock(records){
  const first=records[0],dt=new Date(first.kickoff_at);
  return `<section class="game"><div class="game-head"><strong>${esc(first.event_name)}</strong><span>${esc(LOCAL.format(dt))} local · ${esc(CT.format(dt))}</span></div><div class="players">${records.map(playerCard).join("")}</div></section>`;
}
function render(data){
  if(data.publication_status!=="RESEARCH_ONLY_NOT_PUBLIC_PICKS"||data.model?.public_selector_validated!==false) throw new Error("NFL public-safety contract changed; refusing to render.");
  $("#freshness").textContent=data.created_at ? `Snapshot ${ageText(data.created_at)} · sealed ${String(data.snapshot_sha256||"").slice(0,12)}…` : "Waiting for first sealed NFL snapshot";
  const s=data.summary||{};
  $("#summary").innerHTML=[[s.games,"Games"],[s.candidates,"QB markets"],[s.shadow_only,"Shadow eligible"],[s.quarantined,"Quarantined"]].map(([v,l])=>`<div class="metric"><strong>${v??"—"}</strong><span>${l}</span></div>`).join("");
  const h=data.source_health||{};
  $("#health").innerHTML=`<span><b>${h.primary_candidates??"—"}</b> normalized markets</span><span><b>${h.binding_status_counts?.BOUND??0}</b> exact bindings</span><span><b>${h.bound_inactive_reports??0}</b> bound inactive reports</span><span><b>${h.market_failure_count??0}</b> market failures</span><span><b>${h.inactive_report_failure_count??0}</b> inactive-report failures</span>`;
  const by=new Map();
  for(const r of data.records||[]){if(!by.has(r.event_id))by.set(r.event_id,[]);by.get(r.event_id).push(r);}
  $("#board").innerHTML=[...by.values()].map(gameBlock).join("")||'<div class="error">Waiting for the first verified main-branch NFL shadow snapshot. No research row is being invented to fill the gap.</div>';
}
async function load(){
  try{
    const res=await fetch(`data.json?cb=${Date.now()}`,{cache:"no-store"});
    if(!res.ok) throw new Error(`HTTP ${res.status}`);
    render(await res.json());
  }catch(e){
    $("#freshness").textContent="NFL shadow board unavailable";
    $("#board").innerHTML=`<div class="error"><strong>Unable to load the current research snapshot.</strong><br>${esc(String(e))}</div>`;
  }
}
load();
setInterval(load,60000);
