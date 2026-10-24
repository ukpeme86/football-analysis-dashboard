import math
from datetime import datetime, timedelta, timezone
import requests
import pandas as pd
import streamlit as st

st.set_page_config(page_title='Free Football Analysis Dashboard', page_icon='⚽', layout='wide')
BASE='https://api.football-data.org/v4'
LEAGUES={'Premier League (England)':'PL','La Liga (Spain)':'PD','Bundesliga (Germany)':'BL1','Serie A (Italy)':'SA','Ligue 1 (France)':'FL1','Champions League':'CL','Europa League':'EL','Eredivisie (Netherlands)':'DED','Primeira Liga (Portugal)':'PPL'}

def get(path, token, params=None):
    r=requests.get(BASE+path, headers={'X-Auth-Token':token.strip()}, params=params or {}, timeout=25)
    if r.status_code==401: raise RuntimeError('Token rejected. Check that it was copied correctly.')
    if r.status_code==403: raise RuntimeError('Your free plan does not include this competition/resource. Try another competition.')
    if r.status_code==429: raise RuntimeError('API request limit reached. Wait before trying again.')
    if r.status_code>=400: raise RuntimeError(f'API error {r.status_code}: {r.text[:250]}')
    return r.json()

def pois(lam):
    p=[math.exp(-lam)*lam**k/math.factorial(k) for k in range(11)]
    s=sum(p)
    return [x/s for x in p]

def predict(history, home, away):
    done=[]
    for m in history:
        sc=m.get('score',{}).get('fullTime',{})
        if m.get('status')=='FINISHED' and sc.get('home') is not None and sc.get('away') is not None:
            done.append((m,int(sc['home']),int(sc['away'])))
    done.sort(key=lambda x:x[0].get('utcDate',''),reverse=True)
    def sample(team, venue):
        out=[]
        for m,h,a in done:
            hn=m.get('homeTeam',{}).get('name',''); an=m.get('awayTeam',{}).get('name','')
            if venue=='home' and hn==team: out.append((h,a))
            elif venue=='away' and an==team: out.append((a,h))
            if len(out)>=5: break
        if len(out)<2:
            out=[]
            for m,h,a in done:
                hn=m.get('homeTeam',{}).get('name',''); an=m.get('awayTeam',{}).get('name','')
                if hn==team: out.append((h,a))
                elif an==team: out.append((a,h))
                if len(out)>=5: break
        return out
    hs=sample(home,'home'); aws=sample(away,'away')
    allh=[h for _,h,a in done]; alla=[a for _,h,a in done]
    bh=sum(allh)/len(allh) if allh else 1.35; ba=sum(alla)/len(alla) if alla else 1.10
    hfor=sum(x for x,y in hs)/len(hs) if hs else bh
    hagain=sum(y for x,y in hs)/len(hs) if hs else ba
    afor=sum(x for x,y in aws)/len(aws) if aws else ba
    aagainst=sum(y for x,y in aws)/len(aws) if aws else bh
    lh=max(.15,min(4,(hfor+aagainst)/2)); la=max(.15,min(4,(afor+hagain)/2))
    ph,pa=pois(lh),pois(la); hw=dr=aw=ov15=ov25=un25=btts=0
    for i,pi in enumerate(ph):
        for j,pj in enumerate(pa):
            p=pi*pj
            if i>j: hw+=p
            elif i==j: dr+=p
            else: aw+=p
            if i+j>=2: ov15+=p
            if i+j>=3: ov25+=p
            if i+j<=2: un25+=p
            if i>=1 and j>=1: btts+=p
    return {'Expected home goals':round(lh,2),'Expected away goals':round(la,2),'Home win %':round(hw*100,1),'Draw %':round(dr*100,1),'Away win %':round(aw*100,1),'Over 1.5 %':round(ov15*100,1),'Over 2.5 %':round(ov25*100,1),'Under 2.5 %':round(un25*100,1),'BTTS Yes %':round(btts*100,1),'BTTS No %':round((1-btts)*100,1),'Samples':f'{len(hs)} home-team matches; {len(aws)} away-team matches'}

st.title('⚽ Free Football Analysis Dashboard — V3')
st.write("Today's upcoming fixtures with simple Poisson estimates for 1X2, goals and BTTS. Estimates are not guarantees.")
with st.sidebar:
    st.header('Data settings')
    token=st.text_input('football-data.org API token',type='password',help='Keep this private. Do not upload it to GitHub or send it to anyone.')
    selected=st.multiselect('Competitions',list(LEAGUES),default=['Premier League (England)','La Liga (Spain)','Bundesliga (Germany)','Serie A (Italy)','Ligue 1 (France)'])
    days=st.selectbox('Recent-results window (days)',[30,60,90],index=1)
    go=st.button("Load today's upcoming matches",type='primary')
if not token:
    st.info('Open the sidebar using the arrow at top-left and enter your private football-data.org token.')
    st.stop()
if go:
    now=datetime.now(timezone.utc); today=now.date(); start=(today-timedelta(days=days)).isoformat(); end=today.isoformat()
    rows=[]; errors=[]; bar=st.progress(0)
    for i,name in enumerate(selected):
        code=LEAGUES[name]
        try:
            day=get(f'/competitions/{code}/matches',token,{'dateFrom':today.isoformat(),'dateTo':today.isoformat()}).get('matches',[])
            upcoming=[]
            for m in day:
                kickoff=datetime.fromisoformat(m['utcDate'].replace('Z','+00:00'))
                if kickoff>=now and m.get('status') in ('SCHEDULED','TIMED'): upcoming.append(m)
            if upcoming:
                hist=get(f'/competitions/{code}/matches',token,{'dateFrom':start,'dateTo':end,'status':'FINISHED'}).get('matches',[])
                for m in upcoming:
                    home=m.get('homeTeam',{}).get('name','Home'); away=m.get('awayTeam',{}).get('name','Away')
                    kick=datetime.fromisoformat(m['utcDate'].replace('Z','+00:00')).astimezone().strftime('%Y-%m-%d %H:%M')
                    rows.append({'Competition':name,'Kickoff (device time)':kick,'Home team':home,'Away team':away,**predict(hist,home,away)})
        except Exception as e: errors.append(f'{name}: {e}')
        bar.progress((i+1)/max(1,len(selected)))
    if errors:
        st.warning('Some competitions could not be loaded (often due to free-plan coverage).')
        for e in errors: st.write('• '+e)
    if rows:
        df=pd.DataFrame(rows).sort_values(['Kickoff (device time)','Competition'])
        st.success(f'Loaded {len(df)} upcoming match(es) for today.')
        st.dataframe(df,use_container_width=True,hide_index=True)
        st.download_button('Download analysis as CSV',df.to_csv(index=False).encode(), 'football_analysis_today.csv','text/csv')
        st.caption('Uses recent completed matches from the same competition and a simple Poisson model. It does not include injuries, line-ups, odds or weather. Verify samples and data coverage before relying on estimates.')
    else:
        st.info('No upcoming matches returned for today in the selected competitions, or your free plan does not include their data. Completed matches are intentionally not shown as upcoming.')
else:
    st.info('Enter your token in the sidebar, select competitions, then tap Load today’s upcoming matches.')
