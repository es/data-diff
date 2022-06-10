import unittest
import preql
import time
from data_diff import database as db
from data_diff.diff_tables import TableDiffer, TableSegment, split_space
from parameterized import parameterized, parameterized_class
from .common import CONN_STRINGS, str_to_checksum
import logging

logging.getLogger("diff_tables").setLevel(logging.WARN)
logging.getLogger("database").setLevel(logging.WARN)

CONNS = {k: (preql.Preql(v), db.connect_to_uri(v)) for k, v in CONN_STRINGS.items()}

TYPE_SAMPLES = {
    "int": [127, -3, -9, 37, 15, 127],
    "datetime_no_timezone": [
        "2020-01-01 15:10:10",
        "2022-01-01 15:10:01.131",
        "2022-01-01 15:10:02.020409",
        "2022-01-01 15:10:03.003030",
        "2022-01-01 15:10:04.7",
        "2022-01-01 15:10:05.009900",
    ],
}

DATABASE_TYPES = {
    db.Postgres: {
        # https://www.postgresql.org/docs/current/datatype-numeric.html#DATATYPE-INT
        "int": [
            "smallint",  # 2 bytes
            "int", # 4 bytes
            "bigint", # 8 bytes
        ],
        # https://www.postgresql.org/docs/current/datatype-datetime.html
        "datetime_no_timezone": [
            "timestamp(6) without time zone",
            "timestamp without time zone",
        ]
    },
    db.MySQL: {
         # https://dev.mysql.com/doc/refman/8.0/en/integer-types.html
        "int": [
            "tinyint", # 1 byte
            "smallint", # 2 bytes
            "mediumint", # 3 bytes
            "int", # 4 bytes
            "bigint", # 8 bytes
        ],
        # https://dev.mysql.com/doc/refman/8.0/en/datetime.html
        "datetime_no_timezone": [
            "timestamp(6)",
            "timestamp",
            "datetime(6)"
        ]
    },
    db.Snowflake: {
        # https://docs.snowflake.com/en/sql-reference/data-types-numeric.html#int-integer-bigint-smallint-tinyint-byteint
        "int": [
            # all 38 digits with 0 precision, don't need to test all
            "int",
            # "integer",
            # "bigint",
            # "smallint",
            # "tinyint",
            # "byteint"
        ],
        # https://docs.snowflake.com/en/sql-reference/data-types-datetime.html
        "datetime_no_timezone": [
            # "timestamp(6)",
        ]
    },
}


type_pairs = []
# =>
# { source: (preql, connection)
# target: (preql, connection)
# source_type: (int, tinyint),
# target_type: (int, bigint) }
for source_db, source_type_categories in DATABASE_TYPES.items():
    for target_db, target_type_categories in DATABASE_TYPES.items():
        for type_category, source_types in source_type_categories.items(): # int, datetime, ..
            for source_type in source_types:
                for target_type in target_type_categories[type_category]:
                    if CONNS.get(source_db, False) and CONNS.get(target_db, False):
                        type_pairs.append((
                          source_db,
                          target_db,
                          source_type,
                          target_type,
                          type_category,
                        ))

def expand_params(testcase_func, param_num, param):
    return "%s_%s" %(
        testcase_func.__name__,
        parameterized.to_safe_name("_".join(str(x) for x in param.args)),
    )

class TestDiffCrossDatabaseTables(unittest.TestCase):
    @parameterized.expand(type_pairs, name_func=expand_params)
    def test_wip_int_different(self, source_db, target_db, source_type, target_type, type_category):
        start = time.time()

        self.preql1, self.connection1 = CONNS[source_db][0], CONNS[source_db][1]
        self.preql2, self.connection2 = CONNS[target_db][0], CONNS[target_db][1]

        self.connections = [self.connection1, self.connection2]

        for i, connection in enumerate(self.connections):
            db_type = type(connection)
            table = "a" if i == 0 else "b"
            col_type = source_type if i == 0 else target_type

            connection.query(f"DROP TABLE IF EXISTS {table}", None)
            connection.query("COMMIT", None)

            if db_type == db.MySQL:
                connection.query(f"CREATE TABLE {table}(id int, col {col_type});", None)
            elif db_type == db.Postgres:
                connection.query(f"CREATE TABLE {table}(id serial, col {col_type});", None)
            elif db_type == db.Snowflake:
                connection.query(f"CREATE TABLE {table}(id int, col {col_type});", None)

            connection.query("COMMIT", None)

            for j, sample in enumerate(TYPE_SAMPLES[type_category]):
                connection.query(f"INSERT INTO {table} (id, col) VALUES ({j+1}, '{sample}')", None)
            connection.query("COMMIT", None)

        self.table = TableSegment(self.connection1, ("a",), "id", None, ("col", ))
        self.table2 = TableSegment(self.connection2, ("b",), "id", None, ("col", ))

        self.assertEqual(6, self.table.count())
        self.assertEqual(6, self.table2.count())

        differ = TableDiffer(bisection_threshold=3, bisection_factor=2) # ensure we actually checksum
        diff = list(differ.diff_tables(self.table, self.table2))
        expected = []
        # self.assertEqual(0, differ.stats.get("rows_inspected", 0))
        print(diff)
        self.assertEqual(expected, diff)

        # Ensure that Python agrees with the checksum!
        differ = TableDiffer(bisection_threshold=1000000000)
        diff = list(differ.diff_tables(self.table, self.table2))
        expected = []
        # self.assertEqual(6, differ.stats.get("rows_inspected", 0))
        self.assertEqual(expected, diff)

        duration = time.time() - start
        print(f"source_db={source_db.__name__} target_db={target_db.__name__} source_type={source_type} target_type={target_type} duration={round(duration * 1000, 2)}ms")