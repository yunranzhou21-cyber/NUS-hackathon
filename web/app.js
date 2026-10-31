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
 $('summary').append(node('h2',titles[data.status] || 'Search complete'));
 $('summary').append(node('p',`${data.results.length} results returned · Retrieval ${data.retrieval_ms} ms · Not exhaustive`));
 for(const code of data.reason_codes || [])$('summary').append(node('p',reasons[code] || code));
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
  const history=check.dataset_history;
  card.append(node('p',`Latest observed in CSV: ${history.latest_observed_publication_date || 'Unknown'} · ${history.same_docket_record_ids.length} records for this docket · ${history.is_latest_observed?'Latest observation':'Historical record'} (not a verified legal version)`,'meta'));
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
let requestNumber=0;
$('search-form').addEventListener('submit',async event=>{
 event.preventDefault(); const current=++requestNumber;
 const configuration={};for(const key of ['make','model','serial_number','line_number'])if($(key).value.trim())configuration[key]=$(key).value.trim();
 $('submit').disabled=true;$('submit').textContent='Searching…';
 $('results').replaceChildren();$('raw-panel').hidden=true;
 $('summary').replaceChildren(node('h2','Searching source evidence…'));
 try{
  const response=await fetch('/api/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:$('query').value,configuration,include_history:$('history').checked})});
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
