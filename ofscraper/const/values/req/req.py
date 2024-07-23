CONNECT_TIMEOUT = 100
POOL_CONNECT_TIMEOUT = 100
MAX_CONNECTIONS = 100
API_MAX_CONNECTION = 100
CHUNK_READ_TIMEOUT = 100
TOTAL_TIMEOUT = None

KEEP_ALIVE = 20
KEEP_ALIVE_EXP = 10
PROXY = None
PROXY_MOUNTS = None
PROXY_AUTH = None
MAX_CHUNK_SIZE = 1024 * 1024 * 32
MIN_CHUNK_SIZE = 64  * 1024
CHUNK_UPDATE_COUNT = 12
CHUNK_SIZE_UPDATE_COUNT=15

REQ_SEMAPHORE_MULTI = 5
SCRAPE_PAID_SEMS = 10
SUBSCRIPTION_SEMS = 5
LIKE_MAX_SEMS = 12
MAX_SEMS_BATCH_DOWNLOAD = 12
MAX_SEMS_SINGLE_THREAD_DOWNLOAD = 50
SESSION_MANAGER_SYNC_SEM_DEFAULT = 3
SESSION_MANAGER_SEM_DEFAULT = 10

OF_MIN_WAIT_SESSION_DEFAULT = 2
OF_MAX_WAIT_SESSION_DEFAULT = 6
OF_MIN_WAIT_EXPONENTIAL_SESSION_DEFAULT = 16
OF_MAX_WAIT_EXPONENTIAL_SESSION_DEFAULT = 128
OF_NUM_RETRIES_SESSION_DEFAULT = 10


OF_MIN_WAIT_API = 3
OF_MAX_WAIT_API = 12
OF_AUTH_MIN_WAIT = 3
OF_AUTH_MAX_WAIT = 10


DOWNLOAD_NUM_TRIES_REQ = 5
DOWNLOAD_NUM_TRIES_CHECK_REQ = 1
AUTH_NUM_TRIES = 3


MAX_THREAD_WORKERS = 20

SESSION_SLEEP_INIT = 2
SESSION_SLEEP_INCREASE_TIME_DIFF = 30
MESSAGE_SLEEP_DEFAULT = 0


# ideal chunk
CHUNK_MEMORY_SPLIT = 256
CHUNK_FILE_SPLIT = 256

MAX_READ_SIZE = 1024 * 1024 * 16
