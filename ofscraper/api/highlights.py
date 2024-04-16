r"""
                                                             
 _______  _______         _______  _______  _______  _______  _______  _______  _______ 
(  ___  )(  ____ \       (  ____ \(  ____ \(  ____ )(  ___  )(  ____ )(  ____ \(  ____ )
| (   ) || (    \/       | (    \/| (    \/| (    )|| (   ) || (    )|| (    \/| (    )|
| |   | || (__     _____ | (_____ | |      | (____)|| (___) || (____)|| (__    | (____)|
| |   | ||  __)   (_____)(_____  )| |      |     __)|  ___  ||  _____)|  __)   |     __)
| |   | || (                   ) || |      | (\ (   | (   ) || (      | (      | (\ (   
| (___) || )             /\____) || (____/\| ) \ \__| )   ( || )      | (____/\| ) \ \__
(_______)|/              \_______)(_______/|/   \__/|/     \||/       (_______/|/   \__/
                                                                                      
"""

import asyncio
import contextvars
import logging
import traceback

import ofscraper.classes.sessionmanager as sessionManager
import ofscraper.utils.constants as constants
import ofscraper.utils.progress as progress_utils
from ofscraper.utils.context.run_async import run

log = logging.getLogger("shared")
attempt = contextvars.ContextVar("attempt")


#############################################################################
#### Stories
####
##############################################################################
@run
async def get_stories_post_progress(model_id, c=None):
    tasks = []
    job_progress = progress_utils.stories_progress

    tasks.append(
        asyncio.create_task(scrape_stories(c, model_id, job_progress=job_progress))
    )

    data = await process_stories_tasks(tasks)

    progress_utils.stories_layout.visible = False
    return data


@run
async def get_stories_post(model_id, c=None):
    tasks = []
    with progress_utils.set_up_api_stories():
        tasks.append(
            asyncio.create_task(
                scrape_stories(
                    c, model_id, job_progress=progress_utils.stories_progress
                )
            )
        )
        return await process_stories_tasks(tasks)


async def scrape_stories(c, user_id, job_progress=None) -> list:
    stories = None
    attempt.set(0)
    new_tasks = []
    await asyncio.sleep(1)
    try:
        attempt.set(attempt.get(0) + 1)
        task = (
            job_progress.add_task(
                f"Attempt {attempt.get()}/{constants.getattr('API_NUM_TRIES')} : user id -> {user_id}",
                visible=True,
            )
            if job_progress
            else None
        )
        async with c.requests_async(
            url=constants.getattr("highlightsWithAStoryEP").format(user_id)
        ) as r:
            stories = await r.json_()
            log.debug(
                f"stories: -> found stories ids {list(map(lambda x:x.get('id'),stories))}"
            )
            log.trace(
                "stories: -> stories raw {posts}".format(
                    posts="\n\n".join(
                        list(
                            map(
                                lambda x: f"scrapeinfo stories: {str(x)}",
                                stories,
                            )
                        )
                    )
                )
            )
    except Exception as E:
        await asyncio.sleep(1)
        log.traceback_(E)
        log.traceback_(traceback.format_exc())
        raise E

    finally:
        (job_progress.remove_task(task) if job_progress and task is not None else None)

    return stories, new_tasks


async def process_stories_tasks(tasks):
    responseArray = []
    page_count = 0
    overall_progress = progress_utils.overall_progress
    page_task = overall_progress.add_task(
        f"Stories Pages Progress: {page_count}", visible=True
    )


    seen=set()
    while tasks:
        new_tasks = []
        try:
            for task in asyncio.as_completed(tasks,timeout=constants.getattr("API_TIMEOUT_PER_TASK")):
                try:
                    result,new_tasks_batch = await task
                    new_tasks.extend(new_tasks_batch)
                    page_count = page_count + 1
                    overall_progress.update(
                                page_task,
                                description=f"Stories Content Pages Progress: {page_count}",
                    )
                    new_posts = [
                        post
                        for post in result
                        if post["id"] not in seen and not seen.add(post["id"])
                    ]

                    responseArray.extend(new_posts)
                except asyncio.TimeoutError:
                    log.traceback_("Task timed out")
                    log.traceback_(traceback.format_exc())
                    [ele.cancel() for ele in tasks]
                    break
                except Exception as E:
                    log.traceback_(E)
                    log.traceback_(traceback.format_exc())
                    continue
        except asyncio.TimeoutError:
                log.traceback_("Task timed out")
                log.traceback_(traceback.format_exc())
                [ele.cancel() for ele in tasks]
        tasks=new_tasks


    overall_progress.remove_task(page_task)

    log.trace(
        "stories raw duped {posts}".format(
            posts="\n\n".join(
                list(map(lambda x: f"dupedinfo stories: {str(x)}", responseArray))
            )
        )
    )
    log.trace(f"stories postids {list(map(lambda x:x.get('id'),new_posts))}")
    log.trace(
        "post raw unduped {posts}".format(
            posts="\n\n".join(
                list(map(lambda x: f"undupedinfo stories: {str(x)}", new_posts))
            )
        )
    )
    log.debug(f"[bold]Stories Count without Dupes[/bold] {len(new_posts)} found")

    return new_posts


##############################################################################
#### Highlights
####
##############################################################################


@run
async def get_highlight_post_progress(model_id, c=None):
    highlightLists = await get_highlight_list_progress(model_id, c)
    return await get_highlights_via_list_progress(highlightLists, c)


async def get_highlight_list_progress(model_id, c=None):
    tasks = []
    tasks.append(
        asyncio.create_task(
            scrape_highlight_list(
                c, model_id, job_progress=progress_utils.highlights_progress
            )
        )
    )
    return await process_task_get_highlight_list(tasks)


async def get_highlights_via_list_progress(highlightLists, c=None):
    tasks = []
    [
        tasks.append(
            asyncio.create_task(
                scrape_highlights(c, i, job_progress=progress_utils.highlights_progress)
            )
        )
        for i in highlightLists
    ]
    return await process_task_highlights(tasks)


@run
async def get_highlight_post(model_id, c=None):
    highlightList = await get_highlight_list(model_id, c)
    return await get_highlights_via_list(highlightList, c)


async def get_highlight_list(model_id, c=None):
    with progress_utils.set_up_api_highlights_lists():
        tasks = []
        tasks.append(
            asyncio.create_task(
                scrape_highlight_list(
                    c, model_id, job_progress=progress_utils.highlights_progress
                )
            )
        )
        return await process_task_get_highlight_list(tasks)


async def get_highlights_via_list(highlightLists, c):
    tasks = []
    with progress_utils.set_up_api_highlights():

        [
            tasks.append(
                asyncio.create_task(
                    scrape_highlights(
                        c, i, job_progress=progress_utils.highlights_progress
                    )
                )
            )
            for i in highlightLists
        ]
        return await process_task_highlights(tasks)


async def process_task_get_highlight_list(tasks):
    highlightLists = []

    page_count = 0
    overall_progress = progress_utils.overall_progress

    page_task = overall_progress.add_task(
        f"Highlights List Pages Progress: {page_count}", visible=True
    )
    seen=set()
    while tasks:
        new_tasks = []
        try:
            for task in asyncio.as_completed(tasks,timeout=constants.getattr("API_TIMEOUT_PER_TASK")):
                try:
                    result,new_tasks_batch = await task
                    new_tasks.extend(new_tasks_batch)
                    page_count = page_count + 1
                    overall_progress.update(
                                page_task,
                                description=f"Highlights List Pages Progress: {page_count}",
                    )
                    new_posts = [
                        post
                        for post in result
                        if post["id"] not in seen and not seen.add(post["id"])
                    ]

                    highlightLists.extend(new_posts)
                except asyncio.TimeoutError:
                    log.traceback_("Task timed out")
                    log.traceback_(traceback.format_exc())
                    [ele.cancel() for ele in tasks]
                    break
                except Exception as E:
                    log.traceback_(E)
                    log.traceback_(traceback.format_exc())
                    continue
        except asyncio.TimeoutError:
                log.traceback_("Task timed out")
                log.traceback_(traceback.format_exc())
                [ele.cancel() for ele in tasks]
        tasks=new_tasks

    overall_progress.remove_task(page_task)
    log.trace(f"highlights lists ids {list(map(lambda x:x.get('id'),highlightLists))}")
    log.trace(
        "highlights lists raw unduped {posts}".format(
            posts="\n\n".join(
                list(map(lambda x: f"undupedinfo archive: {str(x)}", highlightLists))
            )
        )
    )
    log.debug(f"[bold]Archived Count without Dupes[/bold] {len(highlightLists)} found")
    return highlightLists


async def process_task_highlights(tasks):
    highlightResponse = []
    page_count = 0
    overall_progress = progress_utils.overall_progress
    page_task = overall_progress.add_task(
        f"Highlight Content via List Pages Progress: {page_count}", visible=True
    )
    seen=set()
    while tasks:
        new_tasks = []
        try:
            for task in asyncio.as_completed(tasks,timeout=constants.getattr("API_TIMEOUT_PER_TASK")):
                try:
                    result,new_tasks_batch = await task
                    new_tasks.extend(new_tasks_batch)
                    page_count = page_count + 1
                    overall_progress.update(
                                page_task,
                                description=f"Highlight Content via List Pages Progress: {page_count}",
                    )
                    new_posts = [
                        post
                        for post in result
                        if post["id"] not in seen and not seen.add(post["id"])
                    ]

                    highlightResponse.extend(new_posts)
                except asyncio.TimeoutError:
                    log.traceback_("Task timed out")
                    log.traceback_(traceback.format_exc())
                    [ele.cancel() for ele in tasks]
                    break
                except Exception as E:
                    log.traceback_(E)
                    log.traceback_(traceback.format_exc())
                    continue
        except asyncio.TimeoutError:
                log.traceback_("Task timed out")
                log.traceback_(traceback.format_exc())
                [ele.cancel() for ele in tasks]
        tasks=new_tasks
    log.trace(f"highlights postids {list(map(lambda x:x.get('id'),highlightResponse))}")
    log.trace(
        "highlights raw unduped {posts}".format(
            posts="\n\n".join(
                list(map(lambda x: f"undupedinfo highlights: {str(x)}", highlightResponse))
            )
        )
    )
    log.debug(f"[bold]Highlights Count without Dupes[/bold] {len(highlightResponse)} found")

    return highlightResponse


async def scrape_highlight_list(c, user_id, job_progress=None, offset=0) -> list:
    new_tasks = []
    await asyncio.sleep(1)
    try:
        attempt.set(attempt.get(0) + 1)
        task = (
            job_progress.add_task(
                f"Attempt {attempt.get()}/{constants.getattr('API_NUM_TRIES')} scraping highlight list  offset-> {offset}",
                visible=True,
            )
            if job_progress
            else None
        )
        async with c.requests_async(
            url=constants.getattr("highlightsWithStoriesEP").format(user_id, offset)
        ) as r:
            resp_data = await r.json_()
            log.trace(f"highlights list: -> found highlights list data {resp_data}")
            data = get_highlightList(resp_data)
            log.debug(f"highlights list: -> found list ids {data}")

    except Exception as E:
        await asyncio.sleep(1)
        log.traceback_(E)
        log.traceback_(traceback.format_exc())
        raise E

    finally:
        (job_progress.remove_task(task) if job_progress and task != None else None)

    return data, new_tasks


async def scrape_highlights(c, id, job_progress=None) -> list:
    new_tasks = []
    await asyncio.sleep(1)
    try:
        attempt.set(attempt.get(0) + 1)
        task = (
            job_progress.add_task(
                f"Attempt {attempt.get()}/{constants.getattr('API_NUM_TRIES')} highlights id -> {id}",
                visible=True,
            )
            if job_progress
            else None
        )
        async with c.requests_async(url=constants.getattr("storyEP").format(id)) as r:
            resp_data = await r.json_()
            log.trace(f"highlights: -> found highlights data {resp_data}")
            log.debug(
                f"highlights: -> found ids {list(map(lambda x:x.get('id'),resp_data['stories']))}"
            )
    except Exception as E:
        await asyncio.sleep(1)
        log.traceback_(E)
        log.traceback_(traceback.format_exc())
        raise E

    finally:
        (job_progress.remove_task(task) if job_progress and task != None else None)

    return resp_data["stories"], new_tasks


def get_highlightList(data):
    for ele in list(filter(lambda x: isinstance(x, list), data.values())):
        if (
            len(
                list(
                    filter(
                        lambda x: isinstance(x.get("id"), int)
                        and data.get("hasMore") != None,
                        ele,
                    )
                )
            )
            > 0
        ):
            return list(map(lambda x: x.get("id"), ele))
    return []


def get_individual_highlights(id):
    return get_individual_stories(id)


def get_individual_stories(id, c=None):
    with sessionManager.sessionManager(
        backend="httpx",
        retries=constants.getattr("API_INDVIDIUAL_NUM_TRIES"),
        wait_min=constants.getattr("OF_MIN_WAIT_API"),
        wait_max=constants.getattr("OF_MAX_WAIT_API"),
    ) as c:
        with c.requests_async(constants.getattr("storiesSPECIFIC").format(id)) as r:
            log.trace(f"highlight raw highlight individua; {r.json_()}")
            return r.json()
