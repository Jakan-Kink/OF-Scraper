r"""
                                                             
        _____                                               
  _____/ ____\______ ________________    ____   ___________ 
 /  _ \   __\/  ___// ___\_  __ \__  \  /  _ \_/ __ \_  __ \
(  <_> )  |  \___ \\  \___|  | \// __ \(  <_> )  ___/|  | \/
 \____/|__| /____  >\___  >__|  (____  /\____/ \___  >__|   
                 \/     \/           \/            \/         
"""
import asyncio
import math
import os
import pathlib
import time
import platform
import shutil
import traceback
import random
import re
import threading
import logging
import logging.handlers
import contextvars
import json
import subprocess
from rich.progress import (
    Progress,
    TimeElapsedColumn,
    DownloadColumn,
    TransferSpeedColumn,
    TextColumn,
    TaskProgressColumn,
    BarColumn,
    TimeRemainingColumn
)

from rich.live import Live
from rich.panel import Panel
from rich.console import Group
from rich.table import Column
from pywidevine.cdm import Cdm
from pywidevine.device import Device
from pywidevine.pssh import PSSH
import arrow
from bs4 import BeautifulSoup


from tenacity import retry,stop_after_attempt,wait_random
import more_itertools
import aioprocessing
import psutil
import queue

import ofscraper.utils.config as config_
import ofscraper.utils.separate as seperate
import ofscraper.db.operations as operations
import ofscraper.utils.paths as paths
import ofscraper.utils.auth as auth
import ofscraper.constants as constants
import ofscraper.utils.dates as dates
import ofscraper.utils.logger as logger
import ofscraper.utils.console as console
import ofscraper.utils.stdout as stdout
import ofscraper.utils.config as config_
import ofscraper.utils.args as args_
import ofscraper.utils.exit as exit
from ofscraper.utils.semaphoreDelayed import semaphoreDelayed
import ofscraper.classes.placeholder as placeholder
import ofscraper.classes.sessionbuilder as sessionbuilder
from   ofscraper.classes.multiprocessProgress import multiprocessProgress as progress
from aioprocessing import AioPipe





from diskcache import Cache
cache = Cache(paths.getcachepath())
attempt = contextvars.ContextVar("attempt")

count_lock=aioprocessing.AioLock()
dir_lock=aioprocessing.AioLock()
chunk_lock=aioprocessing.AioLock()


#progress globals
total_bytes_downloaded = 0
photo_count = 0
video_count = 0
audio_count=0
skipped = 0
data=0
logqueue_=logger.queue_
logqueue2_=logger.otherqueue_


async def process_dicts(username,model_id,medialist):
    log=logging.getLogger("shared")
    random.shuffle(medialist)

    mediasplits=get_mediasplits(medialist)
    num_proc=len(mediasplits)
    split_val=min(4,num_proc)
    log.debug(f"Number of process {num_proc}")
    connect_tuple=[AioPipe() for i in range(num_proc)]

    shared=list(more_itertools.chunked([i for i in range(num_proc)],split_val))
    #ran by main process cause of stdout
    logqueues_=[aioprocessing.AioQueue()  for i in range(len(shared))]
    #ran by other process
    otherqueues_=[aioprocessing.AioQueue()  for i in range(len(shared))]
    
    #start main queue consumers
    logthreads=[logger.start_stdout_logthread(input_=logqueues_[i],name=f"ofscraper_{i+1}",count=len(list(shared[i]))) for i in range(len(shared))]
    #start producers
    logs=[logger.get_shared_logger(main_=logqueues_[i//split_val],other_=otherqueues_[i//split_val],name=f"shared_{i}") for i in range(num_proc) ]
    processes=[ aioprocessing.AioProcess(target=process_dict_starter, args=(username,model_id,mediasplits[i],logs[i],connect_tuple[i][1])) for i in range(num_proc)]
    try:
        [process.start() for process in processes]      
        downloadprogress=config_.get_show_downloadprogress(config_.read_config()) or args_.getargs().downloadbars
        job_progress=progress(TextColumn("{task.description}",table_column=Column(ratio=2)),BarColumn(),
            TaskProgressColumn(),TimeRemainingColumn(),TransferSpeedColumn(),DownloadColumn())      
        overall_progress=Progress(  TextColumn("{task.description}"),
        BarColumn(),TaskProgressColumn(),TimeElapsedColumn())
        progress_group = Group(overall_progress,Panel(Group(job_progress,fit=True)))
        desc = 'Progress: ({p_count} photos, {v_count} videos, {a_count} audios,  {skipped} skipped || {sumcount}/{mediacount}||{data})'   
        task1 = overall_progress.add_task(desc.format(p_count=photo_count, v_count=video_count,a_count=audio_count, skipped=skipped,mediacount=len(medialist), sumcount=video_count+audio_count+photo_count+skipped,data=data), total=len(medialist),visible=True)
        progress_group.renderables[1].height=max(15,console.get_shared_console().size[1]-2) if downloadprogress else 0
        with stdout.lowstdout():
            with Live(progress_group, refresh_per_second=constants.refreshScreen,console=console.get_shared_console()):
                queue_threads=[threading.Thread(target=queue_process,args=(connect_tuple[i][0],overall_progress,job_progress,task1,len(medialist))) for i in range(num_proc)]
                [thread.start() for thread in queue_threads]
                [thread.join() for thread in queue_threads]
                time.sleep(1)
                [logthread.join() for logthread in logthreads]
                [process.join(timeout=1) for process in processes]    
                [process.terminate() for process in processes]    
            overall_progress.remove_task(task1)
            log.error(f'[bold]{username}[/bold] ({photo_count} photos, {video_count} videos, {audio_count} audios,  {skipped} skipped)' )
    except KeyboardInterrupt as E:
            try:
                with exit.DelayedKeyboardInterrupt():
                    [process.terminate() for process in processes]  
                    raise KeyboardInterrupt
            except KeyboardInterrupt:
                    raise KeyboardInterrupt
    except Exception as E:
            try:
                with exit.DelayedKeyboardInterrupt():
                    [process.terminate() for process in processes]  
                    raise E
            except KeyboardInterrupt:
                  raise KeyboardInterrupt  
def queue_process(queue_,overall_progress,job_progress,task1,total):
    count=0
    downloadprogress=config_.get_show_downloadprogress(config_.read_config()) or args_.getargs().downloadbars
    desc = 'Progress: ({p_count} photos, {v_count} videos, {a_count} audios,  {skipped} skipped || {sumcount}/{mediacount}||{data})'
    #shared globals
    global total_bytes_downloaded
    global video_count
    global audio_count
    global photo_count
    global skipped
    global data

    while True:
        if count==1 or overall_progress.tasks[task1].total==overall_progress.tasks[task1].completed:
            break
        results = queue_.recv()
        if not isinstance(results,list):
            results=[results]
        for result in results:
            if result is None:
                count=count+1
                continue 
            if isinstance(result,dict) and not downloadprogress:
                continue
            
            if isinstance(result,dict):
                job_progress_helper(job_progress,result)
                continue

            media_type, num_bytes_downloaded = result
            with count_lock:
                total_bytes_downloaded=total_bytes_downloaded+num_bytes_downloaded
                data = convert_num_bytes(total_bytes_downloaded)
                if media_type == 'images':
                    photo_count += 1 

                elif media_type == 'videos':
                    video_count += 1
                elif media_type == 'audios':
                    audio_count += 1
                elif media_type == 'skipped':
                    skipped += 1
                overall_progress.update(task1,description=desc.format(
                            p_count=photo_count, v_count=video_count, a_count=audio_count,skipped=skipped, data=data,mediacount=total, sumcount=video_count+audio_count+photo_count+skipped), refresh=True, advance=1)     


def get_mediasplits(medialist):
    user_count=config_.get_threads(config_.read_config() or args_.getargs().downloadthreads)
    final_count=min(user_count,len(os.sched_getaffinity(0)), len(medialist)//5)
    return more_itertools.divide(final_count, medialist   )
def process_dict_starter(username,model_id,ele,log,queue_):
    asyncio.run(process_dicts_split(username,model_id,ele,log,queue_))

def job_progress_helper(job_progress,result):
    funct={
      "add_task"  :job_progress.add_task,
      "update":job_progress.update,
      "remove_task":job_progress.remove_task
     }.get(result.pop("type"))
    if funct:
        try:
            with chunk_lock:
                funct(*result.pop("args"),**result)
        except Exception as E:
            logging.getLogger("shared").debug(E)
def setpriority():
    os_used = platform.system() 
    process = psutil.Process(os.getpid())  # Set highest priority for the python script for the CPU
    if os_used == "windows":  # Windows (either 32-bit or 64-bit)
        process.nice(psutil.NORMAL_PRIORITY_CLASS)

    elif os_used == "linux":  # linux
        process.ionice(psutil.IOPRIO_NORMAL)
        process.nice(5) 
    else:  # MAC OS X or other
        process.nice(10) 
        process.ionice(ioclass=2)

async def process_dicts_split(username, model_id, medialist,logCopy,queuecopy):
    global innerlog
    innerlog = contextvars.ContextVar("innerlog")
    logCopy.debug(f"{pid_log_helper()} start inner thread for other loggers")
    #start consumer for other
    other_thread=logger.start_other_thread(input_=logCopy.handlers[1].queue,name=str(os.getpid()),count=1)
    setpriority()

 
    medialist=list(medialist)
    # This need to be here: https://stackoverflow.com/questions/73599594/asyncio-works-in-python-3-10-but-not-in-python-3-8
    global sem
    sem = semaphoreDelayed(config_.get_download_semaphores(config_.read_config()))
    global dirSet
    dirSet=set()
    global split_log
    split_log=logCopy
    global log_trace
    log_trace=True if "TRACE" in set([args_.getargs().log,args_.getargs().output,args_.getargs().discord]) else False
    global queue_
    queue_=queuecopy
    
    split_log.debug(f"{pid_log_helper()} starting process")
    
    

    if not args_.getargs().dupe:
        media_ids = set(operations.get_media_ids(model_id,username))
        split_log.debug(f"{pid_log_helper()} number of unique media ids in database for {username}: {len(media_ids)}")
        medialist = seperate.separate_by_id(medialist, media_ids)
        split_log.debug(f"{pid_log_helper()} Number of new mediaids with dupe ids removed: {len(medialist)}")  
        medialist=seperate.seperate_avatars(medialist)
        split_log.debug(f"{pid_log_helper()} Remove avatar")
        split_log.debug(f"{pid_log_helper()} Final Number of media to downlaod {len(medialist)}")

    else:
        split_log.info(f"{pid_log_helper()} forcing all downloads media count {len(medialist)}")
    file_size_limit = config_.get_filesize()
        
    aws=[]

    async with sessionbuilder.sessionBuilder() as c:
        i=0
        for ele in medialist:
            aws.append(asyncio.create_task(download(c,ele ,model_id, username,file_size_limit)))

        for coro in asyncio.as_completed(aws):
                try:
                    media_type, num_bytes_downloaded = await coro
                    await queue_.coro_send(  (media_type, num_bytes_downloaded))
                except Exception as e:
                    innerlog.get().traceback(e)
                    innerlog.get().traceback(traceback.format_exc())
                    media_type = "skipped"
                    num_bytes_downloaded = 0
                    await queue_.coro_send(  (media_type, num_bytes_downloaded))
            

    setDirectoriesDate()
    split_log.debug(f"{pid_log_helper()} download process thread closing")
    split_log.critical(None)
    await queue_.coro_send(None)
    other_thread.join()
 

def retry_required(value):
    return value == ('skipped', 1)

def pid_log_helper():
    return f"PID: {os.getpid()}"  


async def download(c,ele,model_id,username,file_size_limit):
    # reduce number of logs
    log=logging.getLogger(f"{ele.id}")
    log.setLevel(1)
    innerqueue=queue.Queue()
    log.addHandler(logging.handlers.QueueHandler(innerqueue))
    innerlog.set(log)

    attempt.set(attempt.get(0) + 1)  
    try:
            with paths.set_directory(placeholder.Placeholders().getmediadir(ele,username,model_id)):
                if ele.url:
                    return await main_download_helper(c,ele,pathlib.Path(".").absolute(),file_size_limit,username,model_id)
                elif ele.mpd:
                    return await alt_download_helper(c,ele,pathlib.Path(".").absolute(),file_size_limit,username,model_id)
    except Exception as e:
        innerlog.get().debug(f"Media:{ele.id} Post:{ele.postid} [attempt {attempt.get()}/{constants.NUM_TRIES}] exception {e}")   
        innerlog.get().debug(f"Media:{ele.id} Post:{ele.postid} [attempt {attempt.get()}/{constants.NUM_TRIES}] exception {traceback.format_exc()}")   
        return 'skipped', 1
    finally:
        await logqueue_.coro_put(list(innerqueue.queue))
        await logqueue2_.coro_put(list(innerqueue.queue))
async def main_download_helper(c,ele,path,file_size_limit,username,model_id):
    path_to_file=None

    innerlog.get().debug(f"Media:{ele.id} Post:{ele.postid} Downloading with normal downloader")
    total ,temp,path_to_file=await main_download_downloader(c,ele,path,file_size_limit,username,model_id)

    if not pathlib.Path(temp).exists():
        innerlog.get().debug(f"Media:{ele.id} Post:{ele.postid} [attempt {attempt.get()}/{constants.NUM_TRIES}] {temp} was not created") 
        return "skipped",1
    elif abs(total-pathlib.Path(temp).absolute().stat().st_size)>500:
        innerlog.get().debug(f"Media:{ele.id} Post:{ele.postid} [attempt {attempt.get()}/{constants.NUM_TRIES}] {ele.filename_} size mixmatch target: {total} vs actual: {pathlib.Path(temp).absolute().stat().st_size}")   
        return "skipped",1 
    else:
        innerlog.get().debug(f"Media:{ele.id} Post:{ele.postid} [attempt {attempt.get()}/{constants.NUM_TRIES}] {ele.filename_} size match target: {total} vs actual: {pathlib.Path(temp).absolute().stat().st_size}")   
        innerlog.get().debug(f"Media:{ele.id} Post:{ele.postid} [attempt {attempt.get()}/{constants.NUM_TRIES}] renaming {pathlib.Path(temp).absolute()} -> {path_to_file}")   
        shutil.move(temp,path_to_file)
        addGlobalDir(path)
        if ele.postdate:
            newDate=dates.convert_local_time(ele.postdate)
            innerlog.get().debug(f"Media:{ele.id} Post:{ele.postid} Attempt to set Date to {arrow.get(newDate).format('YYYY-MM-DD HH:mm')}")  
            set_time(path_to_file,newDate )
            innerlog.get().debug(f"Media:{ele.id} Post:{ele.postid} Date set to {arrow.get(path_to_file.stat().st_mtime).format('YYYY-MM-DD HH:mm')}")  

        if ele.id:
            await operations.write_media_table(ele,path_to_file,model_id,username)
        set_cache_helper(ele)
        return ele.mediatype,total
@retry(stop=stop_after_attempt(constants.NUM_TRIES),wait=wait_random(min=constants.OF_MIN, max=constants.OF_MAX),reraise=True) 
async def main_download_downloader(c,ele,path,file_size_limit,username,model_id):
    try:
        url=ele.url
        innerlog.get().debug(f"Media:{ele.id} Post:{ele.postid} Attempting to download media {ele.filename_} with {url}")
        await sem.acquire()
        temp=paths.truncate(pathlib.Path(path,f"{ele.filename}_{ele.id}.part"))
        pathlib.Path(temp).unlink(missing_ok=True) if (args_.getargs().part_cleanup or config_.get_part_file_clean(config_.read_config()) or False) else None
        resume_size=0 if not pathlib.Path(temp).exists() else pathlib.Path(temp).absolute().stat().st_size
        cache.close()
        
        path_to_file=None
       

        async with c.requests(url=url)() as r:
                if r.ok:
                    rheaders=r.headers
                    total = int(rheaders['Content-Length'])
                    if file_size_limit>0 and total > int(file_size_limit): 
                            return total ,"skipped",None 
                       
                    content_type = rheaders.get("content-type").split('/')[-1]
                    filename=placeholder.Placeholders().createfilename(ele,username,model_id,content_type)
                    path_to_file = paths.truncate(pathlib.Path(path,f"{filename}")) 
                else:
                    r.raise_for_status()          
                                   
        if total!=resume_size:
            async with c.requests(url=url,headers={"Range":f"bytes={resume_size}-{total}"})() as r:
                if r.ok:
                    pathstr=str(path_to_file)
                    if not total or (resume_size!=total):
                        await queue_.coro_send({"type":"add_task","args":(f"{(pathstr[:constants.PATH_STR_MAX] + '....') if len(pathstr) > constants.PATH_STR_MAX else pathstr}\n",ele.id),
                                       "total":total,"visible":False})
                        await queue_.coro_send({"type":"update","args":(ele.id,),"completed":resume_size})
                        size=resume_size
                        count=0
                        with open(temp, 'ab') as f: 
                            await queue_.coro_send({"type":"update","args":(ele.id,),"visible":True})
                            async for chunk in r.iter_chunked(1024):
                                count=count+1
                                size=size+len(chunk)
                                innerlog.get().trace(f"Media:{ele.id} Post:{ele.postid} Download:{size}/{total}")
                                f.write(chunk)
                                if count==constants.CHUNK_ITER:await queue_.coro_send({"type":"update","args":(ele.id,),"completed":size});count=0
                            await queue_.coro_send({"type":"remove_task","args":(ele.id,)})
                else:
                    r.raise_for_status() 
                                  
        return total ,temp,path_to_file

    except Exception as E:
        innerlog.get().traceback(traceback.format_exc())
        innerlog.get().traceback(E)
        raise E
    finally:
        sem.release()




async def alt_download_helper(c,ele,path,file_size_limit,username,model_id):
    innerlog.get().debug(f"Media:{ele.id} Post:{ele.postid} Downloading with protected media downloader")      
    innerlog.get().debug(f"Media:{ele.id} Post:{ele.postid} Attempting to download media {ele.filename_} with {ele.mpd}")
    path_to_file = paths.truncate(pathlib.Path(path,f'{placeholder.Placeholders().createfilename(ele,username,model_id,"mp4")}'))
    temp_path=paths.truncate(pathlib.Path(path,f"temp_{ele.id or ele.filename_}.mp4"))
    audio,video=await alt_download_preparer(ele)
    audio=await alt_download_downloader(audio,c,ele,path,file_size_limit)
    video=await alt_download_downloader(video,c,ele,path,file_size_limit)
    if int(file_size_limit)>0 and int(video["total"])+int(audio["total"]) > int(file_size_limit): 
        return 'skipped', 1       
        
    for item in [audio,video]:
        if not pathlib.Path(item["path"]).exists():
                innerlog.get().debug(f"Media:{ele.id} Post:{ele.postid} [attempt {attempt.get()}/{constants.NUM_TRIES}] {item['path']} was not created") 
                return "skipped",1
        elif abs(item["total"]-pathlib.Path(item['path']).absolute().stat().st_size)>500:
            innerlog.get().debug(f"Media:{ele.id} Post:{ele.postid} [attempt {attempt.get()}/{constants.NUM_TRIES}] {item['name']} size mixmatch target: {item['total']} vs actual: {pathlib.Path(item['path']).absolute().stat().st_size}")   
            return "skipped",1 
                
    for item in [audio,video]:

        key=await key_helper_manual(c,item["pssh"],ele.license,ele.id)  if (args_.getargs().key_mode or config_.get_key_mode(config_.read_config()) or "auto") == "manual" \
        else await key_helper(c,item["pssh"],ele.license,ele.id)
        if key==None:
            innerlog.get().debug(f"Media:{ele.id} Post:{ele.postid} Could not get key")
            return "skipped",1 
        innerlog.get().debug(f"Media:{ele.id} Post:{ele.postid} got key")
        newpath=pathlib.Path(re.sub("\.part$","",str(item["path"]),re.IGNORECASE))
        innerlog.get().debug(f"Media:{ele.id} Post:{ele.postid} [attempt {attempt.get()}/{constants.NUM_TRIES}] renaming {pathlib.Path(item['path']).absolute()} -> {newpath}")   
        r=subprocess.run([config_.get_mp4decrypt(config_.read_config()),"--key",key,str(item["path"]),str(newpath)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        if not pathlib.Path(newpath).exists():
            innerlog.get().debug(f"Media:{ele.id} Post:{ele.postid} mp4decrypt failed")
            innerlog.get().debug(f"Media:{ele.id} Post:{ele.postid} mp4decrypt {r.stderr.decode()}")
            innerlog.get().debug(f"Media:{ele.id} Post:{ele.postid} mp4decrypt {r.stdout.decode()}")
        else:
            innerlog.get().debug(f"Media:{ele.id} Post:{ele.postid} mp4decrypt success {newpath}")    
        pathlib.Path(item["path"]).unlink(missing_ok=True)
        item["path"]=newpath
    
    path_to_file.unlink(missing_ok=True)
    temp_path.unlink(missing_ok=True)
    t=subprocess.run([config_.get_ffmpeg(config_.read_config()),"-i",str(video["path"]),"-i",str(audio["path"]),"-c","copy","-movflags", "use_metadata_tags",str(temp_path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if t.stderr.decode().find("Output")==-1:
        innerlog.get().debug(f"Media:{ele.id} Post:{ele.postid} ffmpeg failed")
        innerlog.get().debug(f"Media:{ele.id} Post:{ele.postid} ffmpeg {t.stderr.decode()}")
        innerlog.get().debug(f"Media:{ele.id} Post:{ele.postid} ffmpeg {t.stdout.decode()}")

    video["path"].unlink(missing_ok=True)
    audio["path"].unlink(missing_ok=True)
    innerlog.get().debug(f"Moving intermediate path {temp_path} to {path_to_file}")
    shutil.move(temp_path,path_to_file)
    addGlobalDir(path_to_file)
    if ele.postdate:
        newDate=dates.convert_local_time(ele.postdate)
        innerlog.get().debug(f"Media:{ele.id} Post:{ele.postid} Attempt to set Date to {arrow.get(newDate).format('YYYY-MM-DD HH:mm')}")  
        set_time(path_to_file,newDate )
        innerlog.get().debug(f"Media:{ele.id} Post:{ele.postid} Date set to {arrow.get(path_to_file.stat().st_mtime).format('YYYY-MM-DD HH:mm')}")  
    if ele.id:
        await operations.write_media_table(ele,path_to_file,model_id,username)
    return ele.mediatype,audio["total"]+video["total"]

async def alt_download_preparer(ele):
    mpd=await ele.parse_mpd
    for period in mpd.periods:
                for adapt_set in filter(lambda x:x.mime_type=="video/mp4",period.adaptation_sets):             
                    kId=None
                    for prot in adapt_set.content_protections:
                        if prot.value==None:
                            kId = prot.pssh[0].pssh 
                            break
                    maxquality=max(map(lambda x:x.height,adapt_set.representations))
                    for repr in adapt_set.representations:
                        origname=f"{repr.base_urls[0].base_url_value}"
                        if repr.height==maxquality:
                            video={"origname":origname,"pssh":kId,"type":"video","name":f"tempvid_{origname}"}
                            break
                for adapt_set in filter(lambda x:x.mime_type=="audio/mp4",period.adaptation_sets):             
                    kId=None
                    for prot in adapt_set.content_protections:
                        if prot.value==None:
                            kId = prot.pssh[0].pssh 
                            logger.updateSenstiveDict(kId,"pssh_code")
                            break
                    for repr in adapt_set.representations:
                        origname=f"{repr.base_urls[0].base_url_value}"
                        audio={"origname":origname,"pssh":kId,"type":"audio","name":f"tempaudio_{origname}"}
                        break
    return audio,video
@retry(stop=stop_after_attempt(constants.NUM_TRIES),wait=wait_random(min=constants.OF_MIN, max=constants.OF_MAX),reraise=True) 
async def alt_download_downloader(item,c,ele,path,file_size_limit):
    try:
        base_url=re.sub("[0-9a-z]*\.mpd$","",ele.mpd,re.IGNORECASE)
        url=f"{base_url}{item['origname']}"
        innerlog.get().debug(f"Media:{ele.id} Post:{ele.postid} Attempting to download media {item['origname']} with {url}")
        await sem.acquire()
        params={"Policy":ele.policy,"Key-Pair-Id":ele.keypair,"Signature":ele.signature}   
        temp= paths.truncate(pathlib.Path(path,f"{item['name']}.part"))
        pathlib.Path(temp).unlink(missing_ok=True) if (args_.getargs().part_cleanup or config_.get_part_file_clean(config_.read_config()) or False) else None
        resume_size=0 if not pathlib.Path(temp).exists() else pathlib.Path(temp).absolute().stat().st_size
        total=None
        async with c.requests(url=url,params=params)() as r:
            if r.ok:
                rheaders=r.headers
                total = int(rheaders['Content-Length'])
                if file_size_limit>0 and total > int(file_size_limit): 
                        return total ,None,None 
                r.raise_for_status()  
        if total!=resume_size:
            headers={"Range":f"bytes={resume_size}-{total}"}  
            async with c.requests(url=url,headers=headers,params=params)() as l:                
                if l.ok:
                    pathstr=str(temp)
                    await queue_.coro_send({"type":"add_task","args":(f"{(pathstr[:constants.PATH_STR_MAX] + '....') if len(pathstr) > constants.PATH_STR_MAX else pathstr}\n",ele.id),
                                       "total":total,"visible":False})
                    await queue_.coro_send({"type":"update","args":(ele.id,),"completed":resume_size}) 
                    count=0
                    size=resume_size                  
                    with open(temp, 'ab') as f:                           
                        await queue_.coro_send({"type":"update","args":(ele.id,),"visible":False})
                        async for chunk in l.iter_chunked(1024):
                            count=count+1
                            size=size+len(chunk)
                            innerlog.get().trace(f"Media:{ele.id} Post:{ele.postid} Download:{size}/{total}")
                            f.write(chunk)
                            if count==constants.CHUNK_ITER:await queue_.coro_send({"type":"update","args":(ele.id,),"completed":size});count=0
                    await queue_.coro_send({"type":"remove_task","args":(ele.id,)})
                else:
                    l.raise_for_status()
                    return item
        item["total"]=total
        item["path"]=temp
        return item
              
    except Exception as E:
        innerlog.get().traceback(traceback.format_exc())
        innerlog.get().traceback(E)
        raise E
    finally:
        sem.release()



@retry(stop=stop_after_attempt(constants.NUM_TRIES),wait=wait_random(min=constants.OF_MIN, max=constants.OF_MAX),reraise=True) 
async def key_helper(c,pssh,licence_url,id):
    innerlog.get().debug(f"ID:{id} using auto key helper")
    try:
        out=cache.get(licence_url)
        innerlog.get().debug(f"ID:{id} pssh: {pssh!=None}")
        innerlog.get().debug(f"ID:{id} licence: {licence_url}")
        if out!=None:
            innerlog.get().debug(f"ID:{id} auto key helper got key from cache")
            return out
        headers=auth.make_headers(auth.read_auth())
        headers["cookie"]=auth.get_cookies()
        auth.create_sign(licence_url,headers)
        json_data = {
            'license': licence_url,
            'headers': json.dumps(headers),
            'pssh': pssh,
            'buildInfo': '',
            'proxy': '',
            'cache': True,
        }
        async with c.requests(url='https://cdrm-project.com/wv',method="post",json=json_data)() as r:
            httpcontent=await r.text_()
            innerlog.get().debug(f"ID:{id} key_response: {httpcontent}")
            soup = BeautifulSoup(httpcontent, 'html.parser')
            out=soup.find("li").contents[0]
            cache.set(licence_url,out, expire=constants.KEY_EXPIRY)
            cache.close()
        return out
    except Exception as E:
        innerlog.get().traceback(E)
        innerlog.get().traceback(traceback.format_exc())
        raise E
        

async def key_helper_manual(c,pssh,licence_url,id):
    innerlog.get().debug(f"ID:{id} using manual key helper")
    out=cache.get(licence_url)
    if out!=None:
        innerlog.get().debug(f"ID:{id} manual key helper got key from cache")
        return out
    innerlog.get().debug(f"ID:{id} pssh: {pssh!=None}")
    innerlog.get().debug(f"ID:{id} licence: {licence_url}")

    # prepare pssh
    pssh = PSSH(pssh)


    # load device
    private_key=pathlib.Path(config_.get_private_key(config_.read_config())).read_bytes()
    client_id=pathlib.Path(config_.get_client_id(config_.read_config())).read_bytes()
    device = Device(security_level=3,private_key=private_key,client_id=client_id,type_="ANDROID",flags=None)


    # load cdm
    cdm = Cdm.from_device(device)

    # open cdm session
    session_id = cdm.open()

    
    keys=None
    challenge = cdm.get_license_challenge(session_id, pssh)
    async with c.requests(url=licence_url,method="post",data=challenge)() as r:
        cdm.parse_license(session_id, (await r.content.read()))
        keys = cdm.get_keys(session_id)
        cdm.close(session_id)
    keyobject=list(filter(lambda x:x.type=="CONTENT",keys))[0]
    key="{}:{}".format(keyobject.kid.hex,keyobject.key.hex())
    cache.set(licence_url,key, expire=constants.KEY_EXPIRY)
    return key

                

    


def convert_num_bytes(num_bytes: int) -> str:
    if num_bytes == 0:
      return '0 B'
    num_digits = int(math.log10(num_bytes)) + 1

    if num_digits >= 10:
        return f'{round(num_bytes / 10**9, 2)} GB'
    return f'{round(num_bytes / 10 ** 6, 2)} MB'

               
def set_time(path, timestamp):
    if platform.system() == 'Windows':
        setctime(path, timestamp)
    pathlib.os.utime(path, (timestamp, timestamp))


def get_error_message(content):
    error_content = content.get('error', 'No error message available')
    try:
        return error_content.get('message', 'No error message available')
    except AttributeError:
        return error_content


def set_cache_helper(ele):
    if  ele.postid and ele.responsetype_=="profile":
        cache.set(ele.postid ,True)
        cache.close()


def addGlobalDir(path):
    dirSet.add(path.resolve().parent)
def setDirectoriesDate():
    split_log.info( f" {pid_log_helper()} Setting Date for modified directories")
    output=set()
    rootDir=pathlib.Path(config_.get_save_location(config_.read_config())).resolve()
    for ele in dirSet:
        output.add(ele)
        while ele!=rootDir and ele.parent!=rootDir:
            output.add(ele.parent)
            ele=ele.parent
    split_log.debug(f"Directories list {rootDir}")
    for ele in output:
        with dir_lock:
            set_time(ele,dates.get_current_time())