r"""
                                                             
  _____/ ____\______ ________________    ____   ___________ 
 /  _ \   __\/  ___// ___\_  __ \__  \  /  _ \_/ __ \_  __ \
(  <_> )  |  \___ \\  \___|  | \// __ \(  <_> )  ___/|  | \/
 \____/|__| /____  >\___  >__|  (____  /\____/ \___  >__|   
                 \/     \/           \/            \/         
"""
import time
import asyncio
import logging
import contextvars
import math
from diskcache import Cache
from tenacity import retry,stop_after_attempt,wait_random,retry_if_not_exception_type
from rich.progress import Progress
from rich.progress import (
    Progress,
    TextColumn,
    SpinnerColumn
)
from rich.panel import Panel
from rich.console import Group
from rich.live import Live
from rich.style import Style
import arrow
import ofscraper.constants as constants
from ofscraper.classes.semaphoreDelayed import semaphoreDelayed
from ..utils.paths import getcachepath
import ofscraper.utils.console as console
import ofscraper.utils.args as args_
import ofscraper.classes.sessionbuilder as sessionbuilder

log=logging.getLogger("shared")
attempt = contextvars.ContextVar("attempt")

sem = semaphoreDelayed(constants.MAX_SEMAPHORE)
@retry(retry=retry_if_not_exception_type(KeyboardInterrupt),stop=stop_after_attempt(constants.NUM_TRIES),wait=wait_random(min=constants.OF_MIN, max=constants.OF_MAX),reraise=True)   
async def scrape_timeline_posts(c, model_id,progress, timestamp=None,required_ids=None) -> list:
    global tasks
    global sem
    posts=None
    attempt.set(attempt.get(0) + 1)

    if timestamp and   (float(timestamp)>(args_.getargs().before or arrow.now()).float_timestamp):
        return []
    if timestamp:
        
        log.debug(arrow.get(math.trunc(float(timestamp))))
        ep = constants.timelineNextEP
        url = ep.format(model_id, str(timestamp))
    else:
        ep=constants.timelineEP
        url=ep.format(model_id)
    log.debug(url)
    try:
        task=progress.add_task(f"Attempt {attempt.get()}/{constants.NUM_TRIES}: Timestamp -> {arrow.get(math.trunc(float(timestamp))) if timestamp!=None  else 'initial'}",visible=True)
        await sem.acquire()
        async with  c.requests(url=url)() as r:
            if r.ok:
                progress.remove_task(task)
                posts = (await r.json_())['list']
                log_id=f"timestamp:{arrow.get(math.trunc(float(timestamp))) if timestamp!=None  else 'initial'}"
                if not posts:
                    posts= []
                if len(posts)==0:
                    log.debug(f"{log_id} -> number of post found 0")


                elif len(posts)>0:
                    log.debug(f"{log_id} -> number of post found {len(posts)}")
                    log.debug(f"{log_id} -> first date {posts[0].get('createdAt') or posts[0].get('postedAt')}")
                    log.debug(f"{log_id} -> last date {posts[-1].get('createdAt') or posts[-1].get('postedAt')}")
                    log.debug(f"{log_id} -> found postids {list(map(lambda x:x.get('id'),posts))}")
                    log.trace("{log_id} -> post raw {posts}".format(log_id=log_id,posts=  "\n\n".join(list(map(lambda x:f"scrapeinfo timeline: {str(x)}",posts)))))
                               
                    if required_ids==None:
                        attempt.set(0)
                        tasks.append(asyncio.create_task(scrape_timeline_posts(c, model_id,progress,timestamp=posts[-1]['postedAtPrecise'])))
                    else:
                        [required_ids.discard(float(ele["postedAtPrecise"])) for ele in posts]
                        if len(required_ids)>0 and float((timestamp) or 0)<=max(required_ids):
                            attempt.set(0)
                            tasks.append(asyncio.create_task(scrape_timeline_posts(c, model_id,progress,timestamp=posts[-1]['postedAtPrecise'],required_ids=required_ids)))
            else:
                    log.debug(f"[bold]timeline response status code:[/bold]{r.status}")
                    log.debug(f"[bold]timeline response:[/bold] {await r.text_()}")
                    log.debug(f"[bold]timeline headers:[/bold] {r.headers}")
                    progress.remove_task(task)
                    r.raise_for_status()
    except Exception as E:
        raise E
    finally:
        sem.release()
    return posts



async def get_timeline_post(model_id): 
    
    overall_progress=Progress(SpinnerColumn(style=Style(color="blue")),TextColumn("Getting timeline media...\n{task.description}"))
    job_progress=Progress("{task.description}")
    progress_group = Group(
    overall_progress,
    Panel(Group(job_progress)))

    global tasks
    tasks=[]
    min_posts=50
    responseArray=[]
    page_count=0
    setCache=True


    cache = Cache(getcachepath())
    if not args_.getargs().no_cache: oldtimeline=cache.get(f"timeline_{model_id}",default=[])
    else: oldtimeline=[];setCache=False
    log.trace("oldtimeline {posts}".format(posts=  "\n\n".join(list(map(lambda x:f"oldtimeline: {str(x)}",oldtimeline)))))
    log.debug(f"[bold]Timeline Cache[/bold] {len(oldtimeline)} found")
    oldtimeline=list(filter(lambda x:x.get("postedAtPrecise")!=None,oldtimeline))
    postedAtArray=sorted(list(map(lambda x:float(x["postedAtPrecise"]),oldtimeline)))
    after=args_.getargs().after or (postedAtArray[-1] if len(postedAtArray)>0 else None) or 0
    filteredArray=list(filter(lambda x:x>=after,postedAtArray))
              
    with Live(progress_group, refresh_per_second=5,console=console.get_shared_console()): 
        async with sessionbuilder.sessionBuilder() as c:     
            if len(filteredArray)>min_posts:
                splitArrays=[filteredArray[i:i+min_posts] for i in range(0, len(filteredArray), min_posts)]
                #use the previous split for timestamp
                tasks.append(asyncio.create_task(scrape_timeline_posts(c,model_id,job_progress,required_ids=set(splitArrays[0]),timestamp= args_.getargs().after.float_timestamp if args_.getargs().after else None)))
                [tasks.append(asyncio.create_task(scrape_timeline_posts(c,model_id,job_progress,required_ids=set(splitArrays[i]),timestamp=splitArrays[i-1][-1])))
                for i in range(1,len(splitArrays)-1)]
                # keeping grabbing until nothing left
                tasks.append(asyncio.create_task(scrape_timeline_posts(c,model_id,job_progress,timestamp=splitArrays[-2][-1])))
            else:
                tasks.append(asyncio.create_task(scrape_timeline_posts(c,model_id,job_progress,timestamp=after)))
            page_task = overall_progress.add_task(f' Pages Progress: {page_count}',visible=True)
            while len(tasks)!=0:
                for coro in asyncio.as_completed(tasks):
                    try:
                        result=await coro or []
                    except Exception as E:
                        setCache=False
                        continue
                    page_count=page_count+1
                    overall_progress.update(page_task,description=f'Pages Progress: {page_count}')
                    responseArray.extend(result)
                time.sleep(1)
                tasks=list(filter(lambda x:x.done()==False,tasks))
            overall_progress.remove_task(page_task)
    
    unduped={}
    log.debug(f"[bold]Timeline Count with Dupes[/bold] {len(responseArray)} found")
    for post in responseArray:
        id=post["id"]
        if unduped.get(id):continue
        unduped[id]=post


    log.trace(f"timeline dupeset postids {list(unduped.keys())}")
    log.trace("post raw unduped {posts}".format(posts=  "\n\n".join(list(map(lambda x:f"undupedinfo timeline: {str(x)}",unduped)))))
    log.debug(f"[bold]Timeline Count without Dupes[/bold] {len(unduped)} found")
    if setCache:
        newcache={}
        for post in oldtimeline+list(map(lambda x:{"id":x.get("id"),"postedAtPrecise":x.get("postedAtPrecise")},unduped.values())):
            id=post["id"]
            if newcache.get(id):continue
            newcache[id]=post
        cache.set(f"timeline_{model_id}",list(map(lambda x:{"id":x.get("id"),"postedAtPrecise":x.get("postedAtPrecise")},newcache.values())),expire=constants.RESPONSE_EXPIRY)
        cache.set(f"timeline_check_{model_id}",list(newcache.values()),expire=constants.CHECK_EXPIRY)
        cache.close()
    return list(unduped.values() )                             


def get_individual_post(id,c=None):
    with c.requests(constants.INDVIDUAL_TIMELINE.format(id))() as r:
        if r.ok:
            log.trace(f"message raw individual {r.json()}")
            return r.json()
        else:
            log.debug(f"[bold]individual post response status code:[/bold]{r.status}")
            log.debug(f"[bold]individual post response:[/bold] {r.text_()}")
            log.debug(f"[bold]individual post headers:[/bold] {r.headers}")


