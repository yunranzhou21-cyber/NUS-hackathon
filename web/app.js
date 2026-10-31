'use strict';
const $ = id => document.getElementById(id);
const reasons = {
 NO_EVIDENCE_FOR_QUERY_AND_FILTERS:'No evidence found for this query and its filters. Try different keywords or include historical records.',
 UNSUPPORTED_QUERY_LANGUAGE_OR_TERMS:'Some query terms are unsupported. Use a model, English keywords, or an example query.',
 CONFIGURATION_INCOMPLETE:'Provide the manufacturer and model.', IDENTIFIER_REQUIRED:'Provide the identifier required by the quoted condition.',
 MODEL_NOT_EXACTLY_RESOLVED:'The exact model could not be resolved. Series membership has not been inferred.',
 APPLICABILITY_NOT_FULLY_PARSED:'The conditions were not fully parsed. Automatic evaluation is unavailable.',
 NOT_LATEST_OBSERVED_RECORD_IN_CSV:'This is not the latest observation for this docket in the CSV.',
 DOCUMENT_KIND_NOT_CONFIRMED_PROPOSAL:'This document may be a withdrawal notice or require type review. No automatic comparison was made.',
 PROPOSED_SCOPE_ONLY_EFFECTIVENESS_UNVERIFIED:'Comparison is limited to proposed text. Legal effect is unverified.',
 QUERY_CONFIGURATION_CONFLICT:'The query and configuration fields conflict. Please check your inputs.',
 MULTIPLE_MODELS_NEED_CLARIFICATION:'Multiple models detected. Search one model at a time.',
 MAKE_MISMATCH_OR_UNREGISTERED_ALIAS:'The manufacturer differs or its alias is not registered.',
 SEARCH_TERMS_REQUIRED:'Enter a model or meaningful keywords.',
 HISTORICAL_PROPOSAL_TEXT_ONLY_NOT_CURRENT:'Historical text only. This proposal is not the latest record for its docket, so this is not a current scope.',
 EXACT_PAGE_UNAVAILABLE:'Exact page not available.',
 OPERATIONAL_DECISION_OUT_OF_SCOPE:'Compliance or release decisions are outside what these records can support.',
 REQUESTED_VALUE_NOT_IN_RECORD:'The requested value is not in the record.',
 PROCEDURE_NOT_IN_RECORD:'The procedure is not in the record.',
 EFFECTIVE_STATUS_NOT_ESTABLISHED:'No effective rule is established by these records.',
 IDENTIFIER_NOT_FOUND:'That identifier is not in the supplied data. No similar search was substituted.',
 IDENTIFIER_MUST_BE_NUMERIC:'This rule supports numeric identifiers only.',
 CONFLICTING_IDENTIFIERS:'Conflicting identifiers detected. Please clarify.',
 QUERY_TOO_COMPLEX:'Use a shorter query with fewer keywords.',
 SOURCE_MAKE_MISSING:'The source manufacturer is missing.',
 NO_EXPLICIT_APPLICABILITY_BLOCK:'No explicitly labeled applicability block was found.',
 UNRESOLVED_ADDITIONAL_CONDITIONS:'Additional conditions require review.',
 UNSUPPORTED_MODEL_OR_TABLE_SYNTAX:'The model or table format requires review.',
 UNSUPPORTED_IDENTIFIER_CONDITION:'The identifier condition is not supported.',
 UNSUPPORTED_MODEL_LIST:'The model list requires review.',
 UNCONFIRMED_BLOCK_BOUNDARY:'The complete applicability block could not be confirmed.'
};
const notices={
 AI_NOT_CONFIGURED_USING_LOCAL_RULES:'AI is not configured. Local rules were used instead.',
 AI_CALL_FAILED_USING_LOCAL_RULES:'The AI service did not respond. Local rules were used instead.',
 AI_OUTPUT_INVALID_USING_LOCAL_RULES:'The AI returned an unusable answer and was ignored. Local rules were used instead.'
};
function node(tag, text, cls) { const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls; return n; }
function detail(parent, label) {const d=node('details');d.append(node('summary',label));parent.append(d);return d;}
function evidence(parent, ev, fallback) {
 const box=node('div',undefined,'evidence');
 box.append(node('blockquote',ev.quote || '(No source text)'));
 const source=ev.source || fallback || {};
 box.append(node('p',`${source.docket || ''} · CSV record ${source.csv_record_number || '—'} · Lines ${(source.physical_lines || []).join('–')} · ${ev.field} · Characters [${ev.start}, ${ev.end})`,'source'));
 if(source.citation)box.append(node('p',`${source.citation} · ${source.published_page_range_raw || ''} (document page range)`,'source'));
 if(source.url) { try {const u=new URL(source.url);if(['https:','http:'].includes(u.protocol)){const a=node('a','Open source page ↗');a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';box.append(a);}}catch(_){} }
 parent.append(box);
}
function render(data) {
 $('results').replaceChildren();$('summary').replaceChildren();
 const titles={EVIDENCE_FOUND:'Candidate evidence found',REFUSE:'Unable to provide a reliable answer',NEEDS_CLARIFICATION:'Query needs clarification'};
 titles.INSUFFICIENT_EVIDENCE='Record found, but it cannot answer this';
 $('summary').append(node('h2',titles[data.status] || 'Search complete'));
 if(data.answerability && data.answerability.unmet_requests.length){
  const box=node('div',undefined,'interp');
  box.append(node('strong','What these records cannot tell you'));
  for(const u of data.answerability.unmet_requests){
   box.append(node('p',`You asked about: “${u.asked}”`,'meta'));
   box.append(node('p',u.explanation));
   for(const ev of u.evidence || [])evidence(box,ev);
  }
  box.append(node('p','The matching record is shown below. Nothing was filled in from outside the supplied data.','meta'));
  $('summary').append(box);
 }
 $('summary').append(node('p',`${data.results.length} results returned · Retrieval ${data.retrieval_ms} ms · Not exhaustive`));
 for(const code of data.reason_codes || [])$('summary').append(node('p',reasons[code] || code));
 if(data.interpretation){
  const i=data.interpretation, box=node('div',undefined,'interp');
  const src={llm_validated:'AI (validated)',local_rules:'Local rules'};
  const intents={search_documents:'Search documents',check_proposed_scope:'Check proposed scope',find_history:'Find historical records'};
  box.append(node('strong','How your question was interpreted'));
  box.append(node('span',src[i.source] || i.source,'badge '+(i.source==='llm_validated'?'':'neutral')));
  box.append(node('p',`Intent: ${intents[i.intent] || i.intent}`));
  const entries=Object.entries(i.fields);
  box.append(node('p',entries.length?'Extracted: '+entries.map(([k,v])=>`${k.replace('_',' ')} = ${v}`).join(' · '):'Extracted: no structured fields'));
  for(const r of i.rejected)box.append(node('p',`Discarded ${r.field} “${r.value}”: not found in your text`,'reason'));
  if(i.notice)box.append(node('p',notices[i.notice] || i.notice,'reason'));
  if(i.conflict)box.append(node('p',`The ${i.conflict.replace('_',' ')} in your question differs from the form field. Please resolve it.`,'reason'));
  box.append(node('p',i.sent_to_external_service?'Only your question text was sent to the model. No documents were sent.':'Processed locally. Nothing left this machine.','meta'));
  $('summary').append(box);
 }
 for(const u of data.unknown_identifiers || [])$('summary').append(node('p',`“${u.value}” was not found.${u.did_you_mean.length?' Did you mean: '+u.did_you_mean.join(', ')+'?':''}`,'reason'));
 if(data.query_plan)$('summary').append(node('p',`Parsed configuration: ${JSON.stringify(data.query_plan.configuration)} · Keywords: ${data.query_plan.lexical_terms.join(' / ')}`));
 const kinds={proposal:'Proposed rule',withdrawal_notice:'Withdrawal notice',supplemental_or_revised_proposal:'Supplemental / revised proposal',needs_review:'Document type needs review',unknown:'Unknown document type'};
 for(const item of data.results){
  const card=node('article',undefined,'card'), check=item.configuration_check;
  card.append(node('span',kinds[check.document_kind] || check.document_kind,'badge warn'),node('span','Legal applicability unverified','badge neutral'));
  card.append(node('h3',item.title.text),node('p',`${item.docket} · ${item.record_id}`,'meta'));
  const decision=node('div',undefined,'decision');
  const labels={MATCH:'Matches the quoted proposed scope',NO_MATCH:'Does not match the quoted proposed scope',UNKNOWN:'Text-scope match cannot be determined'};
  decision.append(node('strong',labels[check.scope_decision]));
  for(const code of check.reason_codes)decision.append(node('p',reasons[code] || code,'reason'));
  if(check.missing_fields)decision.append(node('p',`Required fields: ${check.missing_fields.join(' / ')}`));
  card.append(decision);
  const rev=item.revision;
  if(rev && !rev.this_is_latest){
   const newer=rev.versions.find(v=>v.is_latest);
   card.append(node('p',`A newer record exists for this docket: ${newer.record_id} (${newer.publication_date || 'date unknown'}, ${kinds[newer.document_kind] || newer.document_kind}). This one is a historical version.`,'reason'));
  }
  if(rev && rev.versions_in_csv>1){
   const list=detail(card,`Versions of ${item.docket} in this CSV (${rev.versions_in_csv})`);
   for(const v of rev.versions)list.append(node('p',`${v.record_id} · ${v.publication_date || 'date unknown'} · ${kinds[v.document_kind] || v.document_kind}${v.is_latest?' · latest in this CSV':''}${v.record_id===item.record_id?' · (this result)':''}`,'meta'));
   list.append(node('p',rev.note,'meta'));
  }else if(rev){
   card.append(node('p','Only one version of this docket is in the CSV. Latest means latest in this CSV, not a verified legal version.','meta'));
  }
  if(item.match && item.match.matched_terms.length){
   card.append(node('p',`Matched: ${item.match.matched_terms.join(', ')}${item.match.unmatched_terms.length?' · Not found in the shown text: '+item.match.unmatched_terms.join(', '):''}`,'meta'));
  }
  const checks=detail(card,'Configuration checks & supporting evidence');
  for(const c of check.checks){checks.append(node('p',`${c.field}: ${c.result || (c.included && !c.excluded?'Within range':'Outside range or excluded')}`));if(c.evidence)evidence(checks,c.evidence,item.title.evidence.source);}
  for(const ev of check.evidence)evidence(checks,ev,item.title.evidence.source);
  const citations=detail(card,`Inspect source text & citations (${item.evidence.length} passages)`);
  item.evidence.forEach(ev=>evidence(citations,ev));
  const titleSource=detail(card,'Title source citation');evidence(titleSource,item.title.evidence);
  $('results').append(card);
 }
 $('raw').textContent=JSON.stringify(data,null,2);$('raw-panel').hidden=false;
}
const failures={
 AUTH_REJECTED:'The service rejected this key. Nothing was saved.',
 UNREACHABLE:'Could not reach the endpoint. Nothing was saved.',
 SERVICE_ERROR:'The service returned an error. Nothing was saved.',
 UNEXPECTED_RESPONSE:'The endpoint did not respond like an OpenAI-compatible chat API. Nothing was saved.',
 MODEL_OUTPUT_UNUSABLE:'The model replied, but not in the required format. Nothing was saved.',
 INVALID_API_KEY_FORMAT:'The key looks malformed (8–300 characters, no spaces).',
 INVALID_MODEL_NAME:'The model name has unsupported characters.',
 INVALID_BASE_URL:'The endpoint must be https (http is allowed only for localhost) with no credentials.'
};
function applyStatus(s,keepResult){
 const on=!!(s.ai_configured ?? s.configured), was=$('use_ai').disabled;
 $('use_ai').disabled=!on; if(on&&was)$('use_ai').checked=true; if(!on)$('use_ai').checked=false;
 $('ai-disconnect').disabled=!(on&&s.source==='session');
 $('ai-hint').textContent=on?`AI on · ${s.model} @ ${s.host} · ${s.source==='session'?'key held in memory':'key from environment'}`:'AI off. Add a key under AI settings. Local rules are used meanwhile.';
 if(!keepResult)$('ai-result').textContent='';
}
fetch('/api/status').then(r=>r.json()).then(s=>applyStatus(s)).catch(()=>{$('ai-hint').textContent='AI status unavailable. Local rules are used.';});
async function aiConfig(body){
 const r=await fetch('/api/ai-config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 return {ok:r.ok,data:await r.json()};
}
$('ai-connect').addEventListener('click',async()=>{
 const button=$('ai-connect'); button.disabled=true; $('ai-result').textContent='Testing connection…';
 try{
  const {ok,data}=await aiConfig({action:'connect',api_key:$('api_key').value.trim(),model:$('ai_model').value.trim(),base_url:$('ai_base').value.trim()});
  if(!ok){$('ai-result').textContent=failures[data.error]||'Request failed.';return;}
  if(data.test==='ok'){$('api_key').value='';applyStatus(data,true);$('ai-result').textContent='Connected. The key was cleared from this page.';}
  else $('ai-result').textContent=failures[data.reason]||'Connection test failed. Nothing was saved.';
 }catch(_){$('ai-result').textContent='Request failed.';}
 finally{button.disabled=false;}
});
$('ai-disconnect').addEventListener('click',async()=>{
 const {data}=await aiConfig({action:'disconnect'}); applyStatus(data,true); $('ai-result').textContent='Disconnected. The key was forgotten.';
});
let requestNumber=0;
$('search-form').addEventListener('submit',async event=>{
 event.preventDefault(); const current=++requestNumber;
 const configuration={};for(const key of ['make','model','serial_number','line_number'])if($(key).value.trim())configuration[key]=$(key).value.trim();
 $('submit').disabled=true;$('submit').textContent='Searching…';
 $('results').replaceChildren();$('raw-panel').hidden=true;
 $('summary').replaceChildren(node('h2','Searching source evidence…'));
 try{
  const response=await fetch('/api/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:$('query').value,configuration,include_history:$('history').checked,historical_comparison:$('historical').checked,use_ai:$('use_ai').checked})});
  const data=await response.json();if(!response.ok)throw new Error(data.error==='INDEX_UNAVAILABLE'?'Search index unavailable. Run python search.py build.':'Request failed. Check your inputs and try again.');
  if(current===requestNumber)render(data);
 }catch(error){if(current===requestNumber)$('summary').replaceChildren(node('h2','Search could not complete'),node('p',error.message));}
 finally{if(current===requestNumber){$('submit').disabled=false;$('submit').textContent='Search evidence ↗';}}
});
const examples={serial:{query:'Find proposals for EMB-120 serial number 120006',make:'Embraer'},corrosion:{query:'corrosion',make:'Boeing'},unknown:{query:'zzzzUnlistedModel999',make:''}};
for(const button of document.querySelectorAll('[data-example]'))button.addEventListener('click',()=>{
 const sample=examples[button.dataset.example];for(const key of ['query','make','model','serial_number','line_number'])$(key).value=sample[key] || '';
 $('history').checked=false;$('search-form').requestSubmit();
});
