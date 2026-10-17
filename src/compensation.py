"""Literal employer salary evidence. No model calls or career-fit changes."""
from __future__ import annotations
import html
import json
import re
from urllib.parse import urlparse

VERSION='salary-v2'
AMOUNT=r'(?:\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d{5,7}(?:\.\d{1,2})?|\d{2,3}(?:\.\d+)?)\s*[kK]?'
RANGE=re.compile(r'(?P<first>(?:US\$|USD\s*|\$)?\s*'+AMOUNT+r')\s*(?:USD\s*)?(?:/year|per\s+year|annually)?\s*(?:[-\u2013\u2014\ufffd]|to|and)\s*(?P<second>(?:US\$|USD\s*|\$)?\s*'+AMOUNT+r')(?:\s*USD\b)?',re.I)
LABEL=re.compile(r'base\s+(?:salary|pay|compensation)|annual\s+salary|(?:estimated\s+)?yearly\s+salary|salary\s+range|hiring\s+range|pay\s+range',re.I)
TOTAL=re.compile(r'total\s+(?:compensation|cash|pay)|on.target\s+earnings|\bOTE\b',re.I)


def number(value):
    raw=re.sub(r'(?i)USD|US\$|\$|,|\s','',value)
    return float(raw[:-1])*1000 if raw.lower().endswith('k') else float(raw)


def us_only(job):
    from src.scorer import _is_us_or_remote
    location=re.sub(r'\bremote\b','',job.get('location') or '',flags=re.I)
    foreign=re.search(r'\b(canada|toronto|vancouver|ontario|uk|london|india|germany|australia|singapore|europe)\b',location,re.I)
    if foreign:return False
    if _is_us_or_remote(location):return True
    url=urlparse(job.get('url') or '')
    return url.hostname is not None and url.hostname.endswith('.myworkdayjobs.com') and bool(re.search(r'/job/US-[A-Z]{2}(?:-|/|$)',url.path,re.I))


def parse_salary(text,job=None,floor=250000):
    from src.fit_scorer import salary_text
    source=salary_text(text);job=job or {};bands=[]
    matches=list(RANGE.finditer(source))
    occupied=[m.span() for m in matches]
    single=re.compile(r'(?:US\$|USD\s*|\$)\s*'+AMOUNT+r'(?:\s*USD\b)?',re.I)
    matches += [m for m in single.finditer(source) if not any(a<=m.start()<z for a,z in occupied)]
    for match in sorted(matches,key=lambda m:m.start()):
        before=source[max(0,match.start()-1800):match.start()]
        after=source[match.end():match.end()+180]
        labels=list(LABEL.finditer(before))
        is_range=match.re is RANGE
        nearby=before[-150:]+match.group()+after[:45]
        # Google sometimes renders a US annual pay band separately from its label.
        google=job.get('source')=='google' and re.search(r'\bUS:\s*$',before,re.I) and 'USD' in nearby
        if not labels and not google:continue
        label=labels[-1] if labels else None
        if label and not is_range and len(before)-label.end()>120:continue
        if label and len(before)-label.end()>250 and not re.search(r'USD\s+(?:annually|per annum|per year)',match.group()+after[:35],re.I):continue
        recent_label=before[label.end():] if label else before[-120:]
        if re.search(r'(?:bonus|equity|RSU|reimbursement|sign.on|stock)\s*(?:target|range|payment)?\s*[:=]?\s*$',recent_label,re.I):continue
        if label and re.fullmatch(r'pay\s+range',label.group(),re.I):
            if re.search(r'total\s*$',before[max(0,label.start()-15):label.start()],re.I):continue
            if not re.search(r'base\s+(?:salary|pay)',before+after[:300],re.I):continue
        tail=before[label.start():] if label else before[-150:]
        # Total-comp/OTE is never base. A nearby mention of separate bonuses or
        # benefits must not discard a clearly identified base band (Apple).
        later_total=list(TOTAL.finditer(tail))
        if later_total and not re.search(r'base\s+(?:salary|pay).*?(?:range|between|from|:|is)',tail[later_total[-1].end():],re.I):continue
        if re.search(r'^\s*(?:OTE|on.target earnings)\b',after,re.I):continue
        if not is_range and re.search(r'(?:starting\s+at|at\s+least|minimum(?:\s+of)?|from)\s*[:=]?\s*$',before,re.I):continue
        if re.search(r'including\s+(?:base\s+and\s+(?:variable|bonus)|(?:bonus|equity|commission))',after[:100],re.I):continue
        unit_context=before[-65:]+match.group()+after[:35]
        if re.search(r'\b(?:hourly|monthly|weekly|per\s+hour|per\s+month|per\s+week)\b|/(?:hour|hr|month|week)\b',unit_context,re.I):continue
        if re.search(r'\b(?:CAD|AUD|NZD|SGD|HKD|EUR|GBP)\b|C\$|A\$',nearby,re.I):continue
        currency=re.search(r'\bUSD\b|US\$|\bUnited States\b|\bUS:\s*',nearby,re.I)
        if not currency and not ('$' in match.group() and us_only(job)):continue
        try:
            low=number(match.group('first')) if is_range else number(match.group())
            high=number(match.group('second')) if is_range else low
        except (ValueError,IndexError):continue
        if not 10000<=low<=high<=3000000:continue
        quote_start=match.start()-len(tail)
        # The quote is contiguous, literal source text, not a rewritten assertion.
        quote=source[quote_start:match.end()+min(35,len(after))]
        if any(b['min']==low and b['max']==high for b in bands):continue
        bands.append({'min':low,'max':high,'currency':'USD','period':'YEAR','quote':quote,
                      'currency_basis':'explicit' if currency else 'US posting location'})
    status='unknown'
    if bands:
        status='meets_floor' if all(b['min']>floor for b in bands) else 'potentially_meets_floor' if any(b['max']>=floor for b in bands) else 'below_floor'
    return {'status':status,'bands':bands,'verification':'verified' if bands else 'ambiguous_salary' if matches and LABEL.search(source) else 'not_found_in_capture','parser_version':VERSION}


def structured_salary(data):
    """Normalize only typed annual USD base-salary metadata, never equity/OTE."""
    if not isinstance(data,dict) or data.get('currency')!='USD':return ''
    value=data.get('value')
    if not isinstance(value,dict) or str(value.get('unitText','')).upper() not in {'YEAR','ANNUAL','YEARLY'}:return ''
    if 'minValue' in value and 'maxValue' not in value:return ''
    low=value.get('minValue',value.get('value'));high=value.get('maxValue',low)
    if isinstance(low,bool) or isinstance(high,bool):return ''
    try:low=float(low);high=float(high)
    except (ValueError,TypeError):return ''
    if not 10000<=low<=high<=3000000:return ''
    return f'Annual USD base salary: ${low:,.2f} - ${high:,.2f}.'


def ashby_salary(posting):
    comp=posting.get('compensation') or {};parts=[]
    components=list(comp.get('summaryComponents') or [])
    for tier in comp.get('compensationTiers') or []:components.extend(tier.get('components') or [])
    for component in components:
        if component.get('compensationType')!='Salary' or component.get('currencyCode')!='USD' or component.get('interval')!='1 YEAR':continue
        text=structured_salary({'currency':'USD','value':{'unitText':'YEAR','minValue':component.get('minValue'),'maxValue':component.get('maxValue')}})
        if text and text not in parts:parts.append(text)
    return '\n'.join(parts)


def page_salary_metadata(page,job):
    """Read the current JobPosting's JSON-LD. Do not borrow related-job salaries."""
    nodes=[]
    def visit(value):
        if isinstance(value,list):
            for item in value:visit(item)
        elif isinstance(value,dict):
            kind=value.get('@type',[]);kind=[kind] if isinstance(kind,str) else kind
            if 'JobPosting' in kind:nodes.append(value)
            if '@graph' in value:visit(value['@graph'])
    for match in re.finditer(r'<script\b[^>]*type=[\"\']application/ld\+json[\"\'][^>]*>(.*?)</script>',page,re.I|re.S):
        try:visit(json.loads(html.unescape(match.group(1))))
        except (ValueError,TypeError):continue
    def same(node):
        url=node.get('url') or node.get('@id') or ''
        return isinstance(url,str) and url.rstrip('/').split('?')[0]==str(job.get('url','')).rstrip('/').split('?')[0]
    relevant=[n for n in nodes if same(n)] or (nodes if len(nodes)==1 else [])
    return '\n'.join(filter(None,(structured_salary(n.get('baseSalary')) for n in relevant)))
