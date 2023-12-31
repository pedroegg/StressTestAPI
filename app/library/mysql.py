import os
import time
import logging
#import threading
from typing import List, Optional, Tuple, Callable, Union

import mysql.connector.pooling as dbConnector
from mysql.connector.cursor_cext import CMySQLCursorPrepared, CMySQLCursorDict
from mysql.connector.errors import Error as DBError, InterfaceError, InternalError, IntegrityError, Warning as DBWarning, PoolError

logger = logging.getLogger("MYSQL")

class Query():
	def __init__(self, query: str, params: tuple = (), usePrepared: bool = False) -> None:
		self.query = query
		self.params = params
		self.usePrepared = usePrepared

class MySQL:
	_pool = None
	#_lock = threading.Lock()

	def __init__(self):
		self.get_connection_retries = int(os.getenv('MYSQL_GET_POOL_CONNECTION_RETRIES', default=0))
		self.get_connection_timeout = int(os.getenv('MYSQL_GET_POOL_CONNECTION_TIMEOUT_SECONDS', default=0))

		#with MySQL._lock:
		if MySQL._pool is None:
			logger.info('new database connection pool')

			try:
				MySQL._pool = dbConnector.MySQLConnectionPool(
					pool_name=os.getenv('MYSQL_POOL_NAME'),
					pool_size=int(os.getenv('MYSQL_POOL_SIZE')),
					host=os.getenv('MYSQL_HOST'),
					user=os.getenv('MYSQL_USER'),
					password=os.getenv('MYSQL_PASSWORD'),
					raise_on_warnings=True, 
					autocommit=False,
					charset='utf8',
					use_unicode=True,
					time_zone='-03:00',
				)
			
			except DBError as err:
				logger.error('cannot connect to database: {}'.format(err.msg))
				raise
	
	def _get_connection(self) -> dbConnector.PooledMySQLConnection:
		if self.get_connection_timeout == 0 or self.get_connection_retries == 0:
			return self._pool.get_connection()

		last_error = None
		for _ in range(self.get_connection_retries+1):
			try:
				connection = self._pool.get_connection()
			
			except PoolError as e:
				last_error = e
				time.sleep(self.get_connection_timeout/self.get_connection_retries)
				continue

			except:
				raise

			break
		
		else:
			raise last_error

		return connection

	def execute(self, query: str, params: tuple = (), usePrepared: bool = False, autoCommit: bool = True) -> Tuple[list, Optional[int]]:
		#with MySQL._lock:
		connection = self._get_connection()
		
		c_class = CMySQLCursorDict
		if usePrepared and params != ():
			c_class = CMySQLCursorPrepared

		try:
			cursor = connection.cursor(cursor_class=c_class)
			result, column_names, last_inserted_id = execute(cursor, query, params)
		
		except:
			self._rollback(connection)
			raise
		
		else:
			if autoCommit:
				self._commit(connection)

		finally:
			cursor.close()
			connection.close()

		response = []
		for row in result:
			if c_class == CMySQLCursorPrepared:
				r = dict(zip(column_names, row))
				for key in r:
					try:
						r[key] = r[key].decode()
					except (UnicodeDecodeError, AttributeError):
						pass
					
				response.append(r)
			else:
				response.append(row)

		return response, last_inserted_id

	# Only for insert, update or delete queries
	def executeTxQueries(self, queries: List[Query], do_after_each: Callable = None) -> List[int]:
		#with MySQL._lock
		connection: dbConnector.PooledMySQLConnection = self._get_connection()
		last_inserted_ids: List[int] = []

		try:
			for query in queries:
				c_class = CMySQLCursorDict
				if query.usePrepared and query.params != ():
					c_class = CMySQLCursorPrepared

				cursor = connection.cursor(cursor_class=c_class)

				try:
					_, _, last_insert_id = execute(cursor, query.query, query.params)
					if last_insert_id is not None:
						last_inserted_ids.append(last_insert_id)

				except:
					raise

				else:
					if do_after_each is not None:
						do_after_each(query)
				
				finally:
					cursor.close()

		except:
			self._rollback(connection)
			raise

		else:
			self._commit(connection)

		finally:
			connection.close()

		return last_inserted_ids
	
	def query(self, query: str, params: tuple = (), usePrepared: bool = False) -> Query:
		return Query(query, params, usePrepared)
	
	def _rollback(self, connection: dbConnector.PooledMySQLConnection):
		try:
			connection.rollback()
		
		except (DBError, InternalError, InterfaceError) as err:
			logger.debug('error trying to rollback transaction: {}'.format(err.msg))
			raise

	def _commit(self, connection: dbConnector.PooledMySQLConnection):
		try:
			connection.commit()

		except (DBError, InternalError, InterfaceError) as err:
			logger.debug('error trying to commit transaction: {}'.format(err.msg))
			raise

def execute(cursor: Union[CMySQLCursorDict, CMySQLCursorPrepared], query: str, params: tuple) -> Tuple[list, tuple, Optional[int]]:
	try:
		cursor.execute(query, params, multi=False)

	except DBWarning as warn:
		logger.warning(warn.msg)
	
	except DBError as err:
		logger.debug('error executing query "{}" with args "{}": {}'.format(query, params, err.msg))
		raise

	column_names = cursor.column_names
	last_inserted_id = cursor.lastrowid

	try:
		result = cursor.fetchall()
		
	except InterfaceError as err:
		if err.msg == "No result set to fetch from" or err.msg == "No result set to fetch from.":
			return [], (), last_inserted_id
		
		raise
	
	return result, column_names, None
