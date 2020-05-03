import calendar
import datetime
import os

from pandas.io import gbq


def diff_month(d1, d2):
    # Get difference between dates in months
    return (d1.year - d2.year) * 12 + d1.month - d2.month


def add_months(sourcedate, months):
    # Add months to the date
    month = sourcedate.month - 1 + months
    year = sourcedate.year + month // 12
    month = month % 12 + 1
    day = min(sourcedate.day, calendar.monthrange(year, month)[1])
    return datetime.date(year, month, day)


def check_input(subreddits, usernames):
    if subreddits:
        subreddits = '", "'.join(subreddits)
        subreddits = '"' + subreddits + '"'
        subreddits = 'AND subreddit in (' + subreddits + ')'
    else:
        subreddits = ''
    if usernames:
        usernames = '", "'.join(usernames)
        usernames = '"' + usernames + '"'
        usernames = 'AND author in (' + usernames + ')'
    else:
        usernames = ''
    return subreddits, usernames


def construct_query(subreddits, usernames, month, score_limit=None):
    # Construct a query string
    subreddits, usernames = check_input(subreddits, usernames)
    if score_limit is None:
        score = ""
    else:
        score = " AND score >= {}".format(int(score_limit))
    query = """
    SELECT
        id,
        body,
        subreddit,
        author,
        created_utc,
        link_id,
        parent_id,
        score
    FROM [fh-bigquery:reddit_comments.""" + month + """]
    WHERE
        body != '[deleted]'
        AND body != '[removed]'
        AND body NOT LIKE '%has been removed%'
        AND body NOT LIKE '%has been overwritten%'
        AND body NOT LIKE '%performed automatically%'
        AND body NOT LIKE '%bot action performed%'
        AND body NOT LIKE '%autowikibot%'
        AND body NOT LIKE '%I am a bot%'
        AND LENGTH(body) > 0""" + score + subreddits + usernames
    return query


def construct_sample_score_query(subreddits, usernames, month, sample_size,
                                 score_limit=None):
    # Construct a query with sampling top-scoring comments
    subreddits, usernames = check_input(subreddits, usernames)
    if score_limit is None:
        score = ""
    else:
        score = " AND score >= {}".format(int(score_limit))
    query = """
    SELECT
        id,
        body,
        subreddit,
        author,
        created_utc,
        link_id,
        parent_id,
        score
    FROM (
        SELECT
            id,
            body,
            subreddit,
            author,
            created_utc,
            link_id,
            parent_id,
            score,
            ROW_NUMBER() OVER(PARTITION BY subreddit ORDER BY score DESC) as pos
        FROM [fh-bigquery:reddit_comments.""" + month + """]
        WHERE
            body != '[deleted]'
            AND body != '[removed]'
            AND body NOT LIKE '%has been removed%'
            AND body NOT LIKE '%has been overwritten%'
            AND body NOT LIKE '%performed automatically%'
            AND body NOT LIKE '%bot action performed%'
            AND body NOT LIKE '%autowikibot%'
            AND body NOT LIKE '%I am a bot%'
            AND LENGTH(body) > 0""" + score + subreddits + usernames + """
    )
    WHERE pos <= """ + str(sample_size)
    return query


def construct_sample_query(subreddits, usernames, month, sample_size, score_limit=None):
    # Constuct a query string with random sampling
    subreddits, usernames = check_input(subreddits, usernames)
    if score_limit is None:
        score = ""
    else:
        score = " AND score >= {}".format(int(score_limit))
    query = """
    SELECT
        id,
        body,
        subreddit,
        author,
        created_utc,
        link_id,
        parent_id,
        score
    FROM (
        SELECT
            id,
            body,
            subreddit,
            author,
            created_utc,
            link_id,
            parent_id,
            score,
            RAND() as rnd,
            ROW_NUMBER() OVER(PARTITION BY subreddit ORDER BY rnd) as pos
        FROM [fh-bigquery:reddit_comments.""" + month + """]
        WHERE
            body != '[deleted]'
            AND body != '[removed]'
            AND body NOT LIKE '%has been removed%'
            AND body NOT LIKE '%has been overwritten%'
            AND body NOT LIKE '%performed automatically%'
            AND body NOT LIKE '%bot action performed%'
            AND body NOT LIKE '%autowikibot%'
            AND body NOT LIKE '%I am a bot%'
            AND LENGTH(body) > 0""" + score + subreddits + usernames + """
    )
    WHERE pos <= """ + str(sample_size)
    return query


def get_comments(timerange, project_id, private_key,
                 subreddits=None, usernames=None, score_limit=None,
                 comments_per_month=None, top_scores=False, csv_directory=None,
                 verbose=False, configuration=None):
    """
    Obtain Reddit comments using Google BigQuery

    Parameters
    ----------
    timerange: iterable, shape (2,)
        Start and end dates in the '%Y_%m' format.
        Example: ('2016_08', '2017_02')

    project_id: str
        Google BigQuery Account project ID

    private_key: str
        File path to JSON file with service account private key
        https://cloud.google.com/bigquery/docs/reference/libraries

    subreddits: list, optional
        List of subreddit names

    usernames: list, optional
        List of usernames

    score_limit: int, optional
        Score limit for comment retrieving. If None, retrieve all comments.

    comments_per_month: int, optional
        Number of comments to sample from each subbredit per month. If None,
        retrieve all comments.

    top_scores: bool, optional
        If True, sample top-scoring comments in each subreddit
        instead of random sampling.

    csv_directory: str, optional
        CSV directory to save retrieved data. If None, return a DataFrame with
        all comments.

    verobse: bool, optional
        If True, print the name of the table, which is being queried.

    configuration: dict,  optional
        Query config parameters for job processing.

    Returns
    ----------
    dfs: list
        List of pd.DataFrames with comments
    """
    if subreddits and not isinstance(subreddits, list):
        raise ValueError(
            'subreddits argument must be a list, not {}'.format(type(subreddits)))
    if usernames and not isinstance(usernames, list):
        raise ValueError(
            'usernames argument must be a list, not {}'.format(type(usernames)))
    if not usernames and not subreddits:
        raise ValueError(
            'You have to specify a list of subreddits or a list of usernames')

    if (comments_per_month is not None) and \
            not isinstance(comments_per_month, int):
        raise ValueError('comments_per_month must be an integer, not {}'.format(
            type(comments_per_month)))
    if (csv_directory is not None) and \
            not os.path.isdir(csv_directory):
        raise OSError('{} does not exist'.format(csv_directory))
    try:
        iter(timerange)
    except TypeError as e:
        raise TypeError('timerange argument must be an iterable') from e
    try:
        assert len(timerange) == 2
    except AssertionError as e:
        raise ValueError(
            'timerange argument has to contain only two elements') from e

    start = datetime.datetime.strptime(timerange[0], '%Y_%m')
    end = datetime.datetime.strptime(timerange[1], '%Y_%m')
    delta = diff_month(end, start)
    if csv_directory is None:
        dfs = []
    else:
        dfs = None

    for i in range(delta + 1):
        date = add_months(start, i)
        table_name = date.strftime('%Y_%m')
        if verbose:
            print(
                'Querying from [fh-bigquery:reddit_comments.{}]'.format(table_name))
        if comments_per_month is None:
            query = construct_query(
                subreddits, usernames, table_name, score_limit)
        elif top_scores:
            query = construct_sample_score_query(
                subreddits, usernames, table_name, comments_per_month, score_limit)
        else:
            query = construct_sample_query(
                subreddits, usernames, table_name, comments_per_month, score_limit)
        df = gbq.read_gbq(query, project_id=project_id,
                          private_key=private_key, configuration=configuration)
        if csv_directory is None:
            dfs.append(df)
        else:
            filename = os.path.join(csv_directory, table_name + '.csv')
            if os.path.isfile(filename):
                with open(filename, 'a') as f:
                    df.to_csv(f, header=False, index=False)
            else:
                df.to_csv(os.path.join(csv_directory, table_name + '.csv'),
                          index=False)
    return dfs
