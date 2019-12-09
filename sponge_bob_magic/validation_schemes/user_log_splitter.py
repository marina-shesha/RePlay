"""
Библиотека рекомендательных систем Лаборатории по искусственному интеллекту.
"""
from abc import abstractmethod

import pyspark.sql.functions as sf
from pyspark.sql import DataFrame, SparkSession, Window

from sponge_bob_magic.validation_schemes.base_splitter import (Splitter,
                                                               SplitterReturnType)


class UserLogSplitter(Splitter):
    def __init__(self, spark: SparkSession,
                 test_size: float,
                 seed: int):
        """
        Инициализация класса.

        :param seed: сид для разбиения
        :param spark: инициализированная спарк-сессия,
            в рамках которой будет происходить обработка данных
        """
        super().__init__(spark)
        self.test_size = test_size
        self.seed = seed

    @abstractmethod
    def _split_proportion(self, log: DataFrame) -> SplitterReturnType:
        pass

    @abstractmethod
    def _split_quantity(self, log: DataFrame) -> SplitterReturnType:
        pass

    def _core_split(self, log: DataFrame) -> SplitterReturnType:
        if 0 <= self.test_size <= 1:
            train, train, test = self._split_proportion
        elif 1 <= self.test_size:
            train, train, test = self._split_quantity
        else:
            raise ValueError(
                "Значение `test_size` должно быть в диапазоне [0, 1] или "
                f"быть числом больше 1; сейчас test_size={self.test_size}"
            )

        return train, train, test

    @staticmethod
    def _add_random_partition(log: DataFrame, seed: int) -> DataFrame:
        """
        Добавляет в лог колонку случайных чисел `rand` и колонку порядкового 
        номера пользователя `row_num` на основе этого случайного порядка.

        :param log: лог взаимодействия, спарк-датафрейм с колонками
            `[timestamp, user_id, item_id, context, relevance]`
        :returns: лог с добавленными столбцами
        """
        log = log.withColumn("rand", sf.rand(seed))
        res = log.withColumn(
            "row_num",
            sf.row_number().over(Window
                                 .partitionBy("user_id")
                                 .orderBy("rand"))
        ).cache()
        return res

    @staticmethod
    def _add_time_partition(log: DataFrame) -> DataFrame:
        """
        Добавляет в лог столбец порядкового номера пользователя `row_num`
        на основе порядка времени в колонке `timestamp`.

        :param log: лог взаимодействия, спарк-датафрейм с колонками
            `[timestamp, user_id, item_id, context, relevance]`
        :returns: лог с добавленными столбцами
        """
        res = log.withColumn(
            "row_num",
            sf.row_number().over(Window
                                 .partitionBy("user_id")
                                 .orderBy(sf.col("timestamp")
                                          .desc()))
        ).cache()
        return res


class RandomUserLogSplitter(UserLogSplitter):
    """ Класс для деления лога каждого пользователя случайно. """

    def _split_quantity(self, log: DataFrame) -> SplitterReturnType:
        """
        Разбить лог действий пользователей рандомно на обучающую и тестовую
        выборки так, чтобы в тестовой выборке было фиксированное количество
        объектов для каждого пользователя.

        :param log: лог взаимодействия, спарк-датафрейм с колонками
            `[timestamp, user_id, item_id, context, relevance]`
        :return: тройка спарк-датафреймов структуры, аналогичной входной
            `train, predict_input, test`
        """
        res = self._add_random_partition(log, self.seed)

        train = (res
                 .filter(res.row_num > self.test_size)
                 .drop("rand", "row_num"))
        test = (res
                .filter(res.row_num <= self.test_size)
                .drop("rand", "row_num"))

        return train, train, test

    def _split_proportion(self, log: DataFrame) -> SplitterReturnType:
        """
        Разбить лог действий пользователей рандомно на обучающую и тестовую
        выборки так, чтобы в тестовой выборке была фиксированная доля
        объектов для каждого пользователя.

        :param log: лог взаимодействия, спарк-датафрейм с колонками
            `[timestamp, user_id, item_id, context, relevance]`
        :return: тройка спарк-датафреймов структуры, аналогичной входной
            `train, predict_input, test`
        """
        counts = log.groupBy("user_id").count()
        res = self._add_random_partition(log, self.seed)

        res = res.join(counts, on="user_id", how="left")
        res = res.withColumn(
            "frac",
            sf.col("row_num") / sf.col("count")
        ).cache()

        train = (res
                 .filter(res.frac > self.test_size)
                 .drop("rand", "row_num", "count", "frac"))
        test = (res
                .filter(res.frac <= self.test_size)
                .drop("rand", "row_num", "count", "frac"))
        return train, train, test


class ByTimeUserLogSplitter(UserLogSplitter):
    """ Класс для деления лога каждого пользователя по времени. """

    def _split_quantity(self, log: DataFrame) -> SplitterReturnType:
        """
        Разбить лог действий пользователей по времени на обучающую и тестовую
        выборки так, чтобы в тестовой выборке было фиксированное количество
        объектов для каждого пользователя.

        :param log: лог взаимодействия, спарк-датафрейм с колонками
            `[timestamp, user_id, item_id, context, relevance]`
        :return: тройка спарк-датафреймов структуры, аналогичной входной
            `train, predict_input, test`
        """
        res = self._add_time_partition(log)
        train = res.filter(res.row_num > self.test_size).drop("row_num")
        test = res.filter(res.row_num <= self.test_size).drop("row_num")

        return train, train, test

    def _split_proportion(self, log: DataFrame) -> SplitterReturnType:
        """
        Разбить лог действий пользователей по времени на обучающую и тестовую
        выборки так, чтобы в тестовой выборке была фиксированная доля
        объектов для каждого пользователя.

        :param log: лог взаимодействия, спарк-датафрейм с колонками
            `[timestamp, user_id, item_id, context, relevance]`
        :return: тройка спарк-датафреймов структуры, аналогичной входной
            `train, predict_input, test`
        """
        counts = log.groupBy("user_id").count()
        res = self._add_time_partition(log)

        res = res.join(counts, on="user_id", how="left")

        res = res.withColumn(
            "frac",
            sf.col("row_num") / sf.col("count")
        ).cache()

        train = (res
                 .filter(res.frac > self.test_size)
                 .drop("row_num", "count", "frac"))

        test = (res
                .filter(res.frac <= self.test_size)
                .drop("row_num", "count", "frac"))

        return train, train, test
