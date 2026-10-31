'use strict';
const $ = id => document.getElementById(id);
const reasons = {
 MULTIPLE_MANUFACTURERS_NEED_CLARIFICATION:'Multiple manufacturers were named. Use one aircraft configuration at a time.',
 FULL_TEXT_APPLICABILITY_UNRESOLVED:'A complete, unambiguous Applicability section could not be resolved in the fetched text.',
 FULL_TEXT_VERIFICATION_INCOMPLETE:'Official full-text verification has not completed. No full-text applicability conclusion is available.',
 NO_EVIDENCE_FOR_QUERY_AND_FILTERS:'No evidence found for this query and its filters. Try different keywords or include historical records.',
 UNSUPPORTED_QUERY_LANGUAGE_OR_TERMS:'Some query terms are unsupported. Use a model, English keywords, or an example query.',
 CONFIGURATION_INCOMPLETE:'Provide the exact model. Manufacturer is optional; if supplied, it must agree with the source.', IDENTIFIER_REQUIRED:'Provide the identifier required by the quoted condition.',
 EXTERNAL_CONDITION_UNRESOLVED:'Answer the cited bulletin condition below, or leave it unknown.',
 EXTERNAL_CONDITION_ANSWERS_CONFLICT:'Your answers to the same bulletin condition conflict. Please clarify.',
 RELIES_ON_USER_ATTESTED_EXTERNAL_CONDITION:'This comparison relies on your bulletin answer. It has not been independently verified.',
 UNKNOWN_EXTERNAL_CONDITION_ID:'An answer belongs to a different condition or bulletin version. Review it before comparing.',
 INVALID_CONFIGURATION:'Check the configuration field types and values.',
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
 if(ev.field==='full_text'){
  box.append(node('p',`${ev.record_id} · ${source.docket} · ${source.citation || 'Citation unavailable'} · ${ev.section} · Printed pages: ${(source.pages || []).join(', ') || 'not resolved'} · Text characters [${ev.start}, ${ev.end})`,'source'));
 }else box.append(node('p',`${source.docket || ''} · CSV record ${source.csv_record_number || '—'} · Lines ${(source.physical_lines || []).join('–')} · ${ev.field} · Characters [${ev.start}, ${ev.end})`,'source'));
 if(source.citation)box.append(node('p',`${source.citation} · ${source.published_page_range_raw || ''} (document page range)`,'source'));
 if(source.url) { try {const u=new URL(source.url);if(['https:','http:'].includes(u.protocol)){const a=node('a','Open source page ↗');a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';box.append(a);}}catch(_){} }
 parent.append(box);
}
const corpus=node('p','Full-text cache: checking…','meta');corpus.id='corpus';$('summary').before(corpus);
async function refreshCorpus(){
 try{const r=await fetch('/api/corpus');if(!r.ok)return;const s=await r.json();corpus.textContent=`On-demand corpus: ${s.cached_documents} text documents · ${s.pdf_documents} PDFs · ${(s.cache_bytes/1048576).toFixed(2)} MB cached / ${(s.cache_budget_bytes/1048576).toFixed(0)} MB budget · 1,000-page requirement not yet verified`;}
 catch(_){corpus.textContent='Full-text cache status unavailable.';}
}
refreshCorpus();
function attachFullText(card,item,data,onCheck,automatic){
 const panel=node('section',undefined,'fulltext-panel'), status=node('p',undefined,'hint');
 const textButton=node('button','Load official full text','secondary');textButton.type='button';
 const pdfButton=node('button','Load official PDF','secondary');pdfButton.type='button';
 const passages=node('div');
 panel.append(node('strong','Official full text'),status,textButton,pdfButton,passages);card.prepend(panel);
 status.textContent='Candidate from supplied CSV. Full-text verification has not completed.';
 if(item.full_text && item.full_text.status==='UNAVAILABLE'){
  status.textContent='No resolved official link in this record. Online verification is unavailable; supplied CSV evidence is shown below.';
  textButton.disabled=true;pdfButton.disabled=true;return;
 }
 let busy=false;
 async function load(action,retry=false){
  if(busy)return;busy=true;textButton.disabled=true;pdfButton.disabled=true;
  const started=performance.now();
  status.textContent=action==='pdf'?'Fetching the requested PDF…':'Fetching official text in the background…';
  let ready=false;
  try{
   for(let poll=0;poll<70;poll++){
    if(!card.isConnected)return;
    const response=await fetch('/api/fulltext',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({record_id:item.record_id,action,query:data.query,configuration:data.query_plan?.configuration || {},historical_comparison:!!data.historical_comparison,retry:retry && poll===0})});
    const state=await response.json();
    if(!response.ok || state.status==='FAILED' || state.status==='UNAVAILABLE')throw new Error(state.error || 'Full text unavailable');
    if(!card.isConnected)return;
    if(state.status==='READY'){
     ready=true;status.textContent=`Official text cached · ${state.citation || ''} · Fetched ${state.fetched_at} · This request ${((performance.now()-started)/1000).toFixed(1)} s. Legal applicability remains unverified.`;
     if(state.configuration_check){
      item.csv_configuration_check ??= item.configuration_check;
      item.configuration_check=state.configuration_check;
      item.fetched_full_text=state;
      onCheck(state.configuration_check);
      $('raw').textContent=JSON.stringify(data,null,2);
     }
     passages.replaceChildren();
     const sourcePanel=detail(passages,`Fetched source passages (${(state.evidence || []).length})`);
     for(const ev of state.evidence || [])evidence(sourcePanel,ev);
     if(state.pdf_url){const a=node('a','Open cached official PDF ↗');a.href=state.pdf_url;a.target='_blank';a.rel='noopener noreferrer';passages.prepend(a);}
     await refreshCorpus();break;
    }
    await new Promise(resolve=>setTimeout(resolve,600));
   }
   if(!ready)throw new Error('Still fetching. Retry shortly; no full-text conclusion has been produced.');
  }catch(error){
   if(card.isConnected){
    status.textContent=`Full-text verification incomplete: ${error.message}. The CSV comparison below is preliminary.`;
    onCheck({...item.configuration_check,scope_decision:'UNKNOWN',reason_codes:['FULL_TEXT_VERIFICATION_INCOMPLETE'],checks:[],evidence:[],external_conditions:[],required_supplements:[]});
   }
  }finally{busy=false;textButton.disabled=false;pdfButton.disabled=false;}
 }
 textButton.addEventListener('click',()=>load('text',true));
 pdfButton.addEventListener('click',()=>load('pdf',true));
 if(automatic)load('text');
}
let conditionAnswers={};
function configurationDecision(check,item) {
const decision=node('div',undefined,'decision');
if(check.basis==='SUPPLIED_CSV_TEXT_ONLY')decision.append(node('p','Preliminary comparison of supplied CSV text; official full-text verification has not completed.','reason'));
const labels={MATCH:'Matches the quoted proposed scope',NO_MATCH:'Does not match the quoted proposed scope',UNKNOWN:'Text-scope match cannot be determined'};
decision.append(node('strong',labels[check.scope_decision]));
for(const code of check.reason_codes)decision.append(node('p',reasons[code] || code,'reason'));
if(check.missing_fields)decision.append(node('p',`Required fields: ${check.missing_fields.join(' / ')}`));
if((check.external_conditions || check.required_supplements || []).length){
 const inputs=[];
 for(const condition of check.external_conditions || check.required_supplements){
  const label=node('label',condition.question), select=node('select');
  select.setAttribute('aria-label',condition.question);
  for(const [value,text] of [['','Unknown / not verified'],['yes','Yes'],['no','No']]){
   const option=node('option',text);option.value=value;select.append(option);
  }
  if(Object.hasOwn(conditionAnswers,condition.id))select.value=conditionAnswers[condition.id]?'yes':'no';
  label.append(select);decision.append(label);
  evidence(decision,condition.evidence,item.title.evidence.source);
  inputs.push([condition.id,select]);
 }
 decision.append(node('p','Your answers describe this aircraft and the exact cited bulletin version. They are not independent verification.','hint'));
 const apply=node('button','Compare with my answers','secondary');apply.type='button';
 apply.addEventListener('click',()=>{
  conditionAnswers={};
  for(const [id,select] of inputs)if(select.value)conditionAnswers[id]=select.value==='yes';
  $('search-form').requestSubmit();
 });
 decision.append(apply);
}
 return decision;
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
 $('summary').append(node('p',`${data.results.length} results returned · Local retrieval ${data.retrieval_ms} ms · Not exhaustive`));
 $('summary').append(node('p','This candidate assessment uses the supplied CSV. Official full-text checks and their fetch time appear separately on each result.'));
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
  box.append(node('p',i.sent_to_external_service?'Only your question text was sent to the model. Document fetching separately contacts official sources.':'Question interpreted locally. Full-text fetching may contact official document sources.','meta'));
  $('summary').append(box);
 }
 for(const u of data.unknown_identifiers || [])$('summary').append(node('p',`“${u.value}” was not found.${u.did_you_mean.length?' Did you mean: '+u.did_you_mean.join(', ')+'?':''}`,'reason'));
 if(data.query_plan)$('summary').append(node('p',`Parsed configuration: ${JSON.stringify(data.query_plan.configuration)} · Keywords: ${data.query_plan.lexical_terms.join(' / ')}`));
 const kinds={proposal:'Proposed rule',withdrawal_notice:'Withdrawal notice',supplemental_or_revised_proposal:'Supplemental / revised proposal',comment_period_extension:'Comment-period extension',correction_notice:'Correction notice',needs_review:'Document type needs review',unknown:'Unknown document type'};
 for(const item of data.results){
  const card=node('article',undefined,'card'), check=item.configuration_check;
  const kindBadge=node('span',kinds[check.document_kind] || check.document_kind,'badge warn');
  card.append(kindBadge,node('span','Legal applicability unverified','badge neutral'));
  card.append(node('h3',item.title.text),node('p',`${item.docket} · ${item.record_id}`,'meta'));
  let decision=configurationDecision(check,item);
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
  for(const c of check.checks){checks.append(node('p',`${c.field}: ${c.result || (c.included && !c.excluded?'Within range':'Outside range or excluded')}${c.source==='USER_ATTESTED_NOT_VERIFIED'?' · User-provided answer; not independently verified':''}`));if(c.evidence)evidence(checks,c.evidence,item.title.evidence.source);}
  for(const ev of check.evidence)evidence(checks,ev,item.title.evidence.source);
  const citations=detail(card,`Inspect source text & citations (${item.evidence.length} passages)`);
  item.evidence.forEach(ev=>evidence(citations,ev));
  const titleSource=detail(card,'Title source citation');evidence(titleSource,item.title.evidence);
  $('results').append(card);
  attachFullText(card,item,data,updated=>{kindBadge.textContent=kinds[updated.document_kind] || updated.document_kind;const next=configurationDecision(updated,item);decision.replaceWith(next);decision=next;}, data.results.indexOf(item)<3 && $('fetch_fulltext').checked);
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
for(const key of ['query','make','model','serial_number','line_number','manufacturer_serial_number'])$(key).addEventListener('input',()=>{conditionAnswers={};});
$('search-form').addEventListener('submit',async event=>{
 event.preventDefault(); const current=++requestNumber;
 const configuration={};for(const key of ['make','model','serial_number','line_number','manufacturer_serial_number'])if($(key).value.trim())configuration[key]=$(key).value.trim();
 if(Object.keys(conditionAnswers).length)configuration.external_conditions={...conditionAnswers};
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
 conditionAnswers={};
 const sample=examples[button.dataset.example];for(const key of ['query','make','model','serial_number','line_number','manufacturer_serial_number'])$(key).value=sample[key] || '';
 $('history').checked=false;syncAircraft();$('search-form').requestSubmit();
});

const aircraftFields=['make','model','serial_number','line_number','manufacturer_serial_number'];
const aircraftPresets={
 embraer:{make:'Embraer',model:'EMB-120',serial_number:'120006',query:'Find proposals for EMB-120 serial number 120006'},
 boeing:{make:'Boeing',model:'757-200',line_number:'95',query:'FAA-2003-16646'},
 airbus:{make:'Airbus Industrie',model:'A320-211',manufacturer_serial_number:'1516',query:'2002-NM-224-AD',historical:true}
};
const aircraftParts={
 airframe:{title:'Airframe',term:'',description:"Explore the aircraft's source documents, then inspect the cited configuration conditions."},
 wings:{title:'Wings',term:'wing',description:'Search for wing mentions in the source text. A mention alone does not establish applicability.'},
 engines:{title:'Engines',term:'engine',description:'Explore engine references. Engine type and installation conditions still require source evidence.'},
 gear:{title:'Landing gear',term:'landing gear',description:'Explore landing-gear references. Review the exact model and configuration conditions in each source.'}
};
let selectedPart='airframe';
function syncAircraft(){
 const model=$('model').value.trim(), make=$('make').value.trim();
 $('aircraft-heading').textContent=model?[make,model].filter(Boolean).join(' '):'Choose an aircraft to begin';
 $('aircraft-identifiers').textContent=[['serial_number','S/N'],['manufacturer_serial_number','MSN'],['line_number','L/N']].filter(([key])=>$(key).value.trim()).map(([key,label])=>`${label} ${$(key).value.trim()}`).join(' · ') || 'No identifier supplied. Add one if the cited rule requires it.';
 $('plane-title').textContent=`Generic aircraft schematic${model?' · search context '+model:''}`;
 for(const button of document.querySelectorAll('[data-preset]')){
  const preset=aircraftPresets[button.dataset.preset];
  const matches=aircraftFields.every(key=>$(key).value.trim().toLowerCase()===String(preset[key] || '').toLowerCase()) && $('historical').checked===!!preset.historical;
  button.setAttribute('aria-pressed',String(matches));
 }
}
function selectAircraftPart(part){
 selectedPart=part;$('aircraft-map').dataset.active=part;
 for(const button of document.querySelectorAll('[data-part]'))button.setAttribute('aria-pressed',String(button.dataset.part===part));
 $('part-title').textContent=aircraftParts[part].title+' selected';
 $('part-description').textContent=aircraftParts[part].description;
 $('search-part').textContent=`Explore ${aircraftParts[part].term || 'airframe'} mentions ↗`;
}
for(const button of document.querySelectorAll('[data-part]'))button.addEventListener('click',()=>selectAircraftPart(button.dataset.part));
for(const key of aircraftFields)for(const event of ['input','change'])$(key).addEventListener(event,syncAircraft);
$('historical').addEventListener('change',syncAircraft);
function configurationUpdated(message){
 conditionAnswers={};++requestNumber;
 $('results').replaceChildren();$('raw-panel').hidden=true;
 $('summary').replaceChildren(node('h2','Configuration ready'),node('p',message));
 $('submit').disabled=false;$('submit').textContent='Search evidence ↗';syncAircraft();
}
for(const button of document.querySelectorAll('[data-preset]'))button.addEventListener('click',()=>{
 const preset=aircraftPresets[button.dataset.preset];
 for(const key of [...aircraftFields,'query'])$(key).value=preset[key] || '';
 $('historical').checked=!!preset.historical;$('history').checked=!!preset.historical;
 for(const b of document.querySelectorAll('[data-preset]'))b.setAttribute('aria-pressed',String(b===button));
 const message=`Demo configuration loaded. ${preset.historical?'Historical-text comparison is enabled for this example. ':''}Review the values, then select Search evidence.`;
 $('aircraft-feedback').textContent=message;$('autofill-status').textContent='Demo values inserted. Change them to describe your actual aircraft.';
 configurationUpdated(message);selectAircraftPart('airframe');
});
$('search-part').addEventListener('click',()=>{
 const model=$('model').value.trim(), term=aircraftParts[selectedPart].term;
 if(!model && !term){$('aircraft-feedback').textContent='Choose a model or a demo configuration first.';$('model').focus();return;}
 $('query').value=[model,term].filter(Boolean).join(' ');
 $('aircraft-feedback').textContent=`Searching text mentions for ${aircraftParts[selectedPart].title.toLowerCase()}. Component applicability must be checked in the source.`;
 $('search-form').requestSubmit();
});
$('autofill').addEventListener('click',async()=>{
 const query=$('query').value.trim();
 if(!query){$('autofill-status').textContent='Enter a question with a model or identifier first.';$('query').focus();return;}
 const button=$('autofill');button.disabled=true;$('autofill-status').textContent='Reading the stated configuration locally…';
 try{
  const response=await fetch('/api/autofill',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query})});
  const result=await response.json();
  if(!response.ok)throw new Error('Could not extract fields. You can still enter them manually.');
  if(query!==$('query').value.trim()){$('autofill-status').textContent='Question changed. Select autofill again for the new question.';return;}
  if(result.status==='NEEDS_CLARIFICATION'){$('autofill-status').textContent=(result.reason_codes || []).map(c=>reasons[c] || c).join(' ');return;}
  const filled=[],conflicts=[];
  for(const [key,value] of Object.entries(result.fields || {})){
   if(!aircraftFields.includes(key))continue;
   const current=$(key).value.trim();
   if(current && current.toLowerCase()!==value.toLowerCase()){conflicts.push(key.replaceAll('_',' '));continue;}
   if(!current){$(key).value=value;filled.push(key.replaceAll('_',' '));}
  }
  const message=[filled.length?'Filled: '+filled.join(', ')+'.':'No empty fields to fill.',conflicts.length?'Kept your existing '+conflicts.join(', ')+'; resolve differences before searching.':'Review the configuration before searching.'].join(' ');
  $('autofill-status').textContent=message;
  if(filled.length)configurationUpdated(message);
 }catch(error){$('autofill-status').textContent=error.message;}
 finally{button.disabled=false;}
});
fetch('/api/aircraft').then(r=>r.ok?r.json():Promise.reject()).then(catalog=>{
 for(const [key,values] of [['model-options',catalog.models],['make-options',catalog.manufacturers]]){
  const fragment=document.createDocumentFragment();for(const value of values){const option=node('option');option.value=value;fragment.append(option);}$(key).replaceChildren(fragment);
 }
}).catch(()=>{$('aircraft-feedback').textContent='Indexed suggestions are unavailable. Presets and manual inputs still work.';});
syncAircraft();
