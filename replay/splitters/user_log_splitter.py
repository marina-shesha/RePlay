"""
Библиотека рекомендательных систем Лаборатории по искусственному интеллекту.
"""
from typing import Optional, Union

import pyspark.sql.functions as sf
from pyspark.sql import DataFrame, Window

from replay.constants import AnyDataFrame
from replay.splitters.base_splitter import Splitter, SplitterReturnType
from replay.utils import convert2spark


# pylint: disable=too-few-public-methods
class UserSplitter(Splitter):
    """
    Данный сплиттер применяет логику разбиения не на весь лог сразу,
    а для оценок каждого пользователя по отдельности.

    Например, можно отложить для теста последние/случайные k оценок для каждого пользователя.
    Также с помощью параметра ``item_test_size`` можно задать долю оценок, которые необходимо отложить.

    Примеры:
    >>> from replay.session_handler import get_spark_session, State
    >>> spark = get_spark_session(1, 1)
    >>> state = State(spark)

    >>> from replay.splitters import UserSplitter
    >>> import pandas as pd
    >>> data_frame = pd.DataFrame({"user_id": [1,1,1,2,2,2], "item_id": [1,2,3,1,2,3], "relevance": [1,2,3,4,5,6], "timestamp": [1,2,3,3,2,1]})
    >>> data_frame
       user_id  item_id  relevance  timestamp
    0        1        1          1          1
    1        1        2          2          2
    2        1        3          3          3
    3        2        1          4          3
    4        2        2          5          2
    5        2        3          6          1

    По умолчанию в тест откладывается 1 предмет для каждого пользователя
    и реализуется сценарий деления по времени,
    то есть для теста остаются самые последние предметы.

    >>> UserSplitter(seed=80083).split(data_frame)[-1].toPandas()
       user_id  item_id  relevance  timestamp
    0        1        3          3          3
    1        2        1          4          3

    Взять случайные предметы, можно с помощью параметра ``shuffle``:

    >>> UserSplitter(shuffle=True, seed=80083).split(data_frame)[-1].toPandas()
       user_id  item_id  relevance  timestamp
    0        1        2          2          2
    1        2        3          6          1

    Можно указать колчество айтемов, которые необходимо отложить для каждого пользователя:

    >>> UserSplitter(item_test_size=3, shuffle=True, seed=80083).split(data_frame)[-1].toPandas()
       user_id  item_id  relevance  timestamp
    0        1        2          2          2
    1        1        3          3          3
    2        1        1          1          1
    3        2        3          6          1
    4        2        2          5          2
    5        2        1          4          3

    Либо долю:

    >>> UserSplitter(item_test_size=0.67, shuffle=True, seed=80083).split(data_frame)[-1].toPandas()
       user_id  item_id  relevance  timestamp
    0        1        2          2          2
    1        1        3          3          3
    2        2        3          6          1
    3        2        2          5          2

    Параметр `user_test_size` позволяет отобрать для теста заданное количество пользователей

    >>> UserSplitter(user_test_size=1, item_test_size=2, seed=42).split(data_frame)[-1].toPandas().user_id.nunique()
    1

    >>> UserSplitter(user_test_size=0.5, item_test_size=2, seed=42).split(data_frame)[-1].toPandas().user_id.nunique()
    1

    """

    # pylint: disable=too-many-arguments
    def __init__(
        self,
        item_test_size: Union[float, int] = 1,
        user_test_size: Optional[Union[float, int]] = None,
        shuffle=False,
        drop_cold_items: bool = False,
        drop_cold_users: bool = False,
        seed: Optional[int] = None,
    ):
        """
        :param item_test_size: размер тестовой выборки; если от 0 до 1, то в
            тест попадает данная доля объектов у каждого пользователя: если целое
            число большее 1, то в тест попадает заданное число объектов у
            каждого пользователя
        :param user_test_size: аналогично ``item_test_size``, но не сколько
            объектов от каждого пользователя включать в тест, а сколько самих
            пользователей (доля либо количество); если ``None``, то берутся все
            пользователи
        :param shuffle: если ``True``, то берутся случайные оценки, иначе последние из колонки ``timestamp``.
        :param drop_cold_items: исключать ли из тестовой выборки объекты,
           которых нет в обучающей
        :param drop_cold_users: исключать ли из тестовой выборки пользователей,
           которых нет в обучающей
        :param seed: сид для разбиения
        """
        super().__init__(
            drop_cold_items=drop_cold_items, drop_cold_users=drop_cold_users
        )
        self.item_test_size = item_test_size
        self.user_test_size = user_test_size
        self.shuffle = shuffle
        self.seed = seed

    def _get_test_users(self, log: DataFrame,) -> DataFrame:
        """
        отобрать тестовых пользователей

        :param log: стандартный лог взаимодействий
        :return: Spark DataFrame с одной колонкой `user_id`
        """
        all_users = log.select("user_id").distinct()
        user_count = all_users.count()
        if self.user_test_size is not None:
            value_error = False
            if isinstance(self.user_test_size, int):
                if (
                    self.user_test_size >= 1
                    and self.user_test_size < user_count
                ):
                    test_user_count = self.user_test_size
                else:
                    value_error = True
            else:
                if self.user_test_size < 1 and self.user_test_size > 0:
                    test_user_count = all_users.count() * self.user_test_size
                else:
                    value_error = True
            if value_error:
                raise ValueError(
                    f"""
                Недопустимое значение параметра
                user_test_size: {self.user_test_size}
                """
                )
            test_users = (
                all_users.withColumn("rand", sf.rand(self.seed))
                .withColumn(
                    "row_num", sf.row_number().over(Window.orderBy("rand"))
                )
                .filter(f"row_num <= {test_user_count}")
                .drop("rand", "row_num")
            )
        else:
            test_users = all_users
        return test_users

    def _split_proportion(self, log: DataFrame) -> SplitterReturnType:
        """
        Разбивает лог действий пользователей на обучающую и тестовую
        выборки так, чтобы в тестовой выборке была фиксированная доля
        объектов для каждого пользователя. Способ разбиения определяется
        классом-наследником.

        :param log: лог взаимодействия, спарк-датафрейм с колонками
            `[timestamp, user_id, item_id, relevance]`
        :return: спарк-датафреймы структуры, аналогичной входной
            `train, test`
        """

        counts = log.groupBy("user_id").count()
        test_users = self._get_test_users(log).withColumn(
            "test_user", sf.lit(1)
        )
        if self.shuffle:
            res = self._add_random_partition(
                log.join(test_users, how="left", on="user_id")
            )
        else:
            res = self._add_time_partition(
                log.join(test_users, how="left", on="user_id")
            )

        res = res.join(counts, on="user_id", how="left")
        res = res.withColumn(
            "frac", sf.col("row_num") / sf.col("count")
        ).cache()
        train = res.filter(
            f"""
                    frac > {self.item_test_size} OR
                    test_user IS NULL
                """
        ).drop("rand", "row_num", "count", "frac", "test_user")
        test = res.filter(
            f"""
                    frac <= {self.item_test_size} AND
                    test_user IS NOT NULL
                """
        ).drop("rand", "row_num", "count", "frac", "test_user")
        return train, test

    def _split_quantity(self, log: DataFrame) -> SplitterReturnType:
        """
        Разбивает лог действий пользователей на обучающую и тестовую
        выборки так, чтобы в тестовой выборке было фиксированное количество
        объектов для каждого пользователя. Способ разбиения определяется
        классом-наследником.

        :param log: лог взаимодействия, спарк-датафрейм с колонками
            `[timestamp, user_id, item_id, relevance]`
        :return: спарк-датафреймы структуры, аналогичной входной
            `train, test`
        """

        test_users = self._get_test_users(log).withColumn(
            "test_user", sf.lit(1)
        )
        if self.shuffle:
            res = self._add_random_partition(
                log.join(test_users, how="left", on="user_id")
            )
        else:
            res = self._add_time_partition(
                log.join(test_users, how="left", on="user_id")
            )
        train = res.filter(
            f"""
                    row_num > {self.item_test_size} OR
                    test_user IS NULL
                """
        ).drop("rand", "row_num", "test_user")
        test = res.filter(
            f"""
                    row_num <= {self.item_test_size} AND
                    test_user IS NOT NULL
                """
        ).drop("rand", "row_num", "test_user")
        return train, test

    def _core_split(self, log: DataFrame) -> SplitterReturnType:
        if 0 <= self.item_test_size < 1.0:
            train, test = self._split_proportion(log)
        elif self.item_test_size >= 1 and isinstance(self.item_test_size, int):
            train, test = self._split_quantity(log)
        else:
            raise ValueError(
                "Значение `test_size` должно быть в диапазоне [0, 1) или "
                "быть целым числом больше 1; "
                f"сейчас test_size={self.item_test_size}"
            )

        return train, test

    def _add_random_partition(self, dataframe: DataFrame) -> DataFrame:
        """
        Добавляет в датафрейм колонку случайных чисел `rand` и колонку
        порядкового номера пользователя `row_num` на основе этого случайного
        порядка. Пользователи должны лежать в колонке `user_id`.

        :param dataframe: спарк-датафрейм с обязательной колонкой `user_id`
        :returns: датафрейм с добавленными колонками
        """
        dataframe = dataframe.withColumn("rand", sf.rand(self.seed))
        dataframe = dataframe.withColumn(
            "row_num",
            sf.row_number().over(
                Window.partitionBy("user_id").orderBy("rand")
            ),
        ).cache()
        return dataframe

    @staticmethod
    def _add_time_partition(dataframe: DataFrame) -> DataFrame:
        """
        Добавляет в лог столбец порядкового номера пользователя `row_num`
        на основе порядка времени в колонке `timestamp`. Пользователи
        должны лежать в колонке `user_id`.

        :param dataframe: спарк-датафрейм с обязательными колонками
            `[timestamp, user_id]`
        :returns: датафрейм с добавленной колонкой
        """
        res = dataframe.withColumn(
            "row_num",
            sf.row_number().over(
                Window.partitionBy("user_id").orderBy(
                    sf.col("timestamp").desc()
                )
            ),
        ).cache()
        return res


def k_folds(
    log: AnyDataFrame,
    n_folds: Optional[int] = 5,
    seed: Optional[int] = None,
    splitter: Optional[str] = "user",
) -> SplitterReturnType:
    """
    Делит лог внутри каждого пользователя на фолды случайным образом.

    :param log: датафрейм для деления
    :param n_folds: количество фолдов
    :param seed: сид разбиения
    :param splitter: стратегия разбиения на фолды.
        Сейчас доступен только вариант user, который разбивает лог каждого пользователя независимо, случайным образом
    :return: трейн и тест по фолдам
    """
    if splitter not in {"user"}:
        raise ValueError(
            "Недопустимое значение параметра splitter: %s" % splitter
        )
    if splitter == "user":
        dataframe = convert2spark(log).withColumn("rand", sf.rand(seed))
        dataframe = dataframe.withColumn(
            "fold",
            sf.row_number().over(Window.partitionBy("user_id").orderBy("rand"))
            % n_folds,
        ).drop("rand")
        for i in range(n_folds):
            train = dataframe.filter(f"fold != {i}").drop("fold")
            test = dataframe.filter(f"fold == {i}").drop("fold")
            yield train, test