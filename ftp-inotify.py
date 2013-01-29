#!/usr/bin/python2.7
# -*- coding: utf-8 -*-
"""监控某个目录下文件的变化，并同步到FTP服务器上。

可能遇到的问题：
1、如果将本脚本用于内网的FTP同步，那么当分发目标主机比较多的时候，会造成一定的延迟；
2、当在一瞬间有多个文件变化时，最后变化的文件可能有几秒钟的延迟，例如测试时，我们使用脚本touch 9个文件，并echo一些内容到文件中，一共执行了5秒的时间；

=====================================
已知不支持同步的操作：

shell> mkdir -p dir1/dir11
不支持使用-p参数一次性创建多级目录，需要拆分成多条命令分别创建目录。
-------

在很短的时间内，创建并删除目录，会导致错误，但不会导致守护程序中断，终端会有类似下面提示：
[2013-01-23 09:55:31,472 pyinotify ERROR] add_watch: cannot watch /tmp/the-test/dir5 WD=-1, Errno=No such file or directory (ENOENT)
-------

短时间内快速创建并删除文件，会导致上传FTP失败。
-------
"""

import pyinotify
import os, sys, mimetypes, logging, getopt, traceback, signal
from logging import handlers
import re, string
from ftplib import FTP
from socket import _GLOBAL_DEFAULT_TIMEOUT


################################################################################
# 以下内容是守护进程的配置信息，请根据提示进行对应配置
################################################################################
#
# 是否开启调试模式
#
#IS_DEBUG = True
IS_DEBUG = False

#
# 指定PID文件
# 默认在/var/run目录下建立和当前脚本同名的pid文件
# 例如：/var/run/ftp-inotify.pid
#
PID_FILE = os.path.join('/var/run/', os.path.splitext(os.path.basename(sys.argv[0]))[0]+'.pid')

#
# 日志文件存储路径
# 需要注意的是，当启用DEBUG模式时，日志文件为配置的LOG_FILE后加.debug后缀
# 当关闭DEBUG模式时，LOG_FILE每天0点轮询。
# 开启和关闭DEBUG模式，参考IS_DEBUG配置项
# 使用None，则日志从终端直接输出，不推荐使用
#
# LOG_FILE = None
# 在/var/log/下，创建和脚本同名的日志路径，例如：/var/log/ftp-inotify
LOG_FILE = os.path.join('/var/log/', os.path.splitext(os.path.basename(sys.argv[0]))[0], 'daily.log')

#
# 监视文件变化的根路径
#
WATCH_PATH = '/tmp/the-test/'

#
# 配置上传的FTP账号
#
UPLOAD_FTP_HOST = ''
UPLOAD_FTP_USER = ''
UPLOAD_FTP_PASS = ''

#
# 允许同步的文件类型
# 值为正则表达式
#
FILE_TYPES = 'image,text'
FILE_EXTENSIONS  = 'jpg,jpeg,png,gif,txt,js,css'

################################################################################
# 配置信息结束
# 后续代码如果您不了解，请勿修改
################################################################################

#
# 初始化日志
#
synclogger = logging.getLogger()
default_hdlr = logging.StreamHandler()

if IS_DEBUG:
    synclogger.setLevel(logging.NOTSET)
else:
    synclogger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s %(message)s')
#formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
default_hdlr.setFormatter(formatter)
synclogger.addHandler(default_hdlr)

###############################################
# 检测上述配置信息是否填写完整并正确
###############################################
# 检查日志路径是否正确
if LOG_FILE:
    LOG_DIR = os.path.dirname(LOG_FILE)
    if not os.path.exists(LOG_DIR):
        try:
            os.makedirs(LOG_DIR)
            synclogger.info('The dir of logs NOT exists, created now: path=%s' % LOG_DIR)
        except Exception as e:
            synclogger.error('The dir of logs NOT exists and created failed, path=%s.' % LOG_DIR)
            print >>sys.stderr, 'The dir of logs NOT exists and created failed, path=%s.' % LOG_DIR
            sys.exit()
    elif not os.access(LOG_DIR, os.W_OK):
        synclogger.error('The dir of logs is NOT writeable: path=%s.' % LOG_DIR)
        print >>sys.stderr, 'The dir of logs is NOT writeable: path=%s.' % LOG_DIR
        sys.exit()
    else:
        # 检查完日志文件设置的有效性后，就不再使用终端输出日志的方式了
        if IS_DEBUG:
            hdlr = logging.FileHandler(LOG_FILE + '.debug')
            synclogger.setLevel(logging.NOTSET)
            synclogger.debug('The log file is: path=%s.debug.' % LOG_FILE)
        else:
            hdlr = logging.handlers.TimedRotatingFileHandler(LOG_FILE, when='midnight')
            synclogger.setLevel(logging.INFO)
            synclogger.debug('The dir of logs is: path=%s.' % LOG_DIR)
        
        hdlr.setFormatter(formatter)
        synclogger.addHandler(hdlr)
        
        synclogger.removeHandler(default_hdlr)

# 检查监视路径是否正确
if not WATCH_PATH:
    synclogger.error('The WATCH_PATH setting MUST be set.')
    print >>sys.stderr, 'The WATCH_PATH setting MUST be set.'
    sys.exit()
else:
    if os.path.exists(WATCH_PATH):
        synclogger.info('Found watch path: path=%s.' % (WATCH_PATH))
    else:
        synclogger.error('The watch path NOT exists, daemon stop now: path=%s.' % (WATCH_PATH))
        print >>sys.stderr, 'The watch path NOT exists, daemon stop now: path=%s.' % (WATCH_PATH)
        sys.exit()

# 允许同步的文件类型（转换成正则表达式）
if FILE_TYPES:
    FILE_TYPES_PATTERN = re.compile("^(%s)/" % re.sub(' *, *', '|', FILE_TYPES))
    synclogger.debug('Watch file types: %s.' % FILE_TYPES)
else:
    synclogger.debug('Watch all types of file.')

# 允许同步的文件扩展名（转换成正则表达式）
if FILE_EXTENSIONS:
    FILE_EXTENSIONS_PATTERN = re.compile("^(%s)$" % re.sub(' *, *', '|', FILE_EXTENSIONS))
    synclogger.debug('Watch file extensions: %s.' % FILE_EXTENSIONS)
else:
    synclogger.debug('Watch all extensions of file.')

# FTP主机地址
if UPLOAD_FTP_HOST:
    synclogger.debug('Found host for ftp server: %s.' % (UPLOAD_FTP_HOST))
else:
    synclogger.error('The host for ftp server MUST be set.')
    print >>sys.stderr, 'The host for ftp server MUST be set.' % (UPLOAD_FTP_HOST)
    sys.exit()

# FTP用户名
if UPLOAD_FTP_USER:
    synclogger.debug('Found username for ftp server: %s.' % (UPLOAD_FTP_USER))
else:
    synclogger.error('The username for ftp server MUST be set.')
    print >>sys.stderr, 'The username for ftp server MUST be set.' % (UPLOAD_FTP_USER)
    sys.exit()

# FTP密码
if UPLOAD_FTP_PASS:
    synclogger.debug('Found password for ftp server: %s.' % (UPLOAD_FTP_PASS))
else:
    synclogger.error('The password for ftp server MUST be set.')
    print >>sys.stderr, 'Found password for ftp server: %s.' % (UPLOAD_FTP_PASS)
    sys.exit()

###############################################
# 完成配置文件检测
###############################################

# The watch manager stores the watches and provides operations on watches
wm = pyinotify.WatchManager()

# 监控事件类型
mask = pyinotify.IN_DELETE | \
       pyinotify.IN_CLOSE_WRITE | \
       pyinotify.IN_ISDIR | \
       pyinotify.IN_CREATE | \
       pyinotify.IN_MOVED_TO | \
       pyinotify.IN_MOVED_FROM

class mgftp(FTP):
    """自定义FTP类，主要增加断线重新连接功能。"""
    
    ftp = None
    
    ftp_host = None
    ftp_user = None
    ftp_pass = None
    ftp_acct = None
    ftp_timeout = 0
    
    def __init__(self, host='', user='', passwd='', acct='', timeout=_GLOBAL_DEFAULT_TIMEOUT):
        if host    : self.ftp_host    = host
        if user    : self.ftp_user    = user
        if passwd  : self.ftp_pass    = passwd
        if acct    : self.ftp_acct    = acct
        if timeout : self.ftp_timeout = timeout
        self.ftp = FTP(host, user, passwd, acct, timeout)

    def reconnect(self):
        if self.ftp_host:
            self.ftp.connect(self.ftp_host)
            if self.ftp_user:
                try:
                    self.ftp.login(self.ftp_user, self.ftp_pass, self.ftp_acct)
                    synclogger.info('Reonnected to ftp server success.')
                    return True
                except Exception as e:
                    synclogger.info('Reonnected to ftp server failed: error=%s.' % e)
                    return False
        else:
            synclogger.info('Reonnected to ftp server failed, host is EMPTY.')
            return False

    def is_alive(self):
        """检查当前连接是否正常"""
        try:
            self.ftp.voidcmd('NOOP')
            return True
        except:
            return False

    def storlines(self, cmd, fp, callback=None):
        try:
            return self.ftp.storlines(cmd, fp, callback)
        except:
            self.reconnect()
            return self.ftp.storlines(cmd, fp, callback)
        
    def storbinary(self, cmd, fp, blocksize=8192, callback=None, rest=None):
        try:
            return self.ftp.storbinary(cmd, fp, blocksize, callback, rest)
        except:
            self.reconnect()
            return self.ftp.storbinary(cmd, fp, blocksize, callback, rest)
        
    def rmd(self, dirname):
        try:
            return self.ftp.rmd(dirname)
        except:
            self.reconnect()
            return self.ftp.rmd(dirname)
        
    def delete(self, filename):
        try:
            return self.ftp.delete(filename)
        except:
            self.reconnect()
            return self.ftp.delete(filename)
        
    def mkd(self, dirname):
        try:
            return self.ftp.mkd(dirname)
        except:
            self.reconnect()
            return self.ftp.mkd(dirname)
        
    def rename(self, fromname, toname):
        try:
            return self.ftp.rename(fromname, toname)
        except:
            self.reconnect()
            return self.ftp.rename(fromname, toname)

class UploadFtp():
    """FTP连接和关闭类，其它功能参考ftplib"""

    c = None
    @staticmethod
    def connect():
        """连接远程FTP服务器，如果已有连接则使用旧连接，如果连接出错（如超时）会重新连接。"""
        if UploadFtp.c is None:
            UploadFtp.c = mgftp(UPLOAD_FTP_HOST, UPLOAD_FTP_USER, UPLOAD_FTP_PASS)
            synclogger.info('Connected to ftp server success.')
            
        return UploadFtp.c

    @staticmethod
    def close():
        """关闭远程连接FTP"""
        if UploadFtp.c is not None:
            try:
                UploadFtp.c.quit()
                synclogger.info('Closed to ftp server success.')
            except Exception as e:
                synclogger.error('Closing to ftp server failed: %s.' % (e))

            UploadFtp.c = None

            return True
        else:
            return False

    @staticmethod
    def path(local_path):
        """根据本地路径得到远程FTP路径"""
        return local_path.replace(WATCH_PATH, '')

    @staticmethod
    def ignore(path):
        """判断是否需要忽略该文件"""
        if os.path.exists(path):
            synclogger.debug("The path exists, checking type now, path=%s." % (path))

            if not FILE_TYPES:
                synclogger.warning("Filter types is NOT setting, sync all types.")
                return False
            elif os.path.isdir(path):
                synclogger.debug("The path is dir, will be synced.")
                return False

            type, encoding = mimetypes.guess_type(path)
            synclogger.debug("The path's info: path=%s, type=%s, encoding=%s." % (path, type, encoding))

            if type is not None and FILE_TYPES_PATTERN.match(type):
                synclogger.debug("Matched type: path=%s, type=%s,  condition=%s." % (path, type, FILE_TYPES_PATTERN.pattern))
                return False

            return True
        else:
            # 如果监测到删除操作
            # 因为操作后，路径已经不存在了，因此无法判断文件类型
            # 只能通过文件路径的扩展名进行猜测
            synclogger.debug("The path NOT exists, checking extension now, path=%s." % (path))

            fileName, fileExtension = os.path.splitext(path)

            # 没有扩展名，默认猜测是目录
            if not fileExtension:
                return False

            if FILE_EXTENSIONS_PATTERN.match(fileExtension):
                synclogger.debug("Matched type: path=%s, ext=%s, condition=%s." % (path, fileExtension, FILE_EXTENSIONS_PATTERN.pattern))
                return False

    @staticmethod
    def istextfile(filename, blocksize = 512):
        """判断文件是否是文本文件"""
        return UploadFtp.istext(open(filename).read(blocksize))

    @staticmethod
    def istext(s):
        """判断字符串是否是文本"""
        if "\0" in s:
            return 0

        if not s:  # Empty files are considered text
            return 1

        text_characters = "".join(map(chr, range(32, 127)) + list("\n\r\t\b"))
        _null_trans = string.maketrans("", "")

        # Get the non-text characters (maps a character to itself then
        # use the 'remove' option to get rid of the text characters.)
        t = s.translate(_null_trans, text_characters)

        # If more than 30% non-text characters, then
        # this is considered a binary file
        if float(len(t))/len(s) > 0.30:
            return 0
        return 1

class EventHandler(pyinotify.ProcessEvent):
    """针对各种磁盘操作的响应方法"""

    def process_IN_CLOSE_WRITE(self, event):
        ftp = UploadFtp.connect()

        # check ingore
        if UploadFtp.ignore(event.pathname):
            synclogger.info("Ignore file: path=%s." % (event.pathname))
            return None

        synclogger.debug("Wrote file success: path=%s." % (event.pathname))

        try:
            if UploadFtp.istextfile(event.pathname):
                ftp.storlines("STOR " + UploadFtp.path(event.pathname), open(event.pathname, "r"))
                synclogger.info("Uploaded text file: local=%s, remote=%s." % (event.pathname, UploadFtp.path(event.pathname)))
            else:
                ftp.storbinary("STOR " + UploadFtp.path(event.pathname), open(event.pathname, "rb"), 1024)
                synclogger.info("Uploaded binary file: local=%s, remote=%s." % (event.pathname, UploadFtp.path(event.pathname)))
        except Exception as e:
            synclogger.info("Uploaded file failed: local=%s, remote=%s, error=%s." % (event.pathname, UploadFtp.path(event.pathname), e))

    def process_IN_DELETE(self, event):
        ftp = UploadFtp.connect()

        # check ingore
        if UploadFtp.ignore(event.pathname):
            synclogger.info("Ignore file: path=%s." % (event.pathname))
            return None

        if event.mask & pyinotify.IN_ISDIR:
            synclogger.debug("Removed dir: path=%s." % (event.pathname))

            try:
                wm.rm_watch(event.pathname, rec=True)
                synclogger.debug("Removed dir to watch list: path=%s." % (event.pathname))
            except Exception as e:
                synclogger.error("Removing dir to watch list failed: path=%s, error=%s." % (event.pathname, e))

            try:
                ftp.rmd(UploadFtp.path(event.pathname))
                synclogger.info("Removed dir from server: path=%s." % (UploadFtp.path(event.pathname)))
            except Exception as e:
                synclogger.error("Removing dir from server failed: path=%s, error=%s." % (UploadFtp.path(event.pathname), e))
        else:
            synclogger.debug("Removed file: path=%s." % (event.pathname))
            try:
                ftp.delete(UploadFtp.path(event.pathname))
                synclogger.info("Removed file from server: path=%s." % (UploadFtp.path(event.pathname)))
            except Exception as e:
                synclogger.error("Removing file from server failed: path=%s, error=%s." % (UploadFtp.path(event.pathname), e))

    def process_IN_CREATE(self, event):
        ftp = UploadFtp.connect()

        # check ingore
        if UploadFtp.ignore(event.pathname):
            synclogger.info("Ignore file: path=%s." % (event.pathname))
            return None

        if event.mask & pyinotify.IN_ISDIR:
            synclogger.debug("Created new dir: path=%s." % (event.pathname))

            try:
                wm.add_watch(event.pathname, mask, rec=True)
                synclogger.debug("Add new dir to watch list: path=%s." % (event.pathname))
            except Exception as e:
                synclogger.error("Add new dir to watch list failed: path=%s, error=%s." % (event.pathname, e))

            try:
                ftp.mkd(UploadFtp.path(event.pathname))
                synclogger.info("Created new dir in server: path=%s." % (UploadFtp.path(event.pathname)))
            except Exception as e:
                synclogger.error("Created new dir in server failed: path=%s, error=%s." % (UploadFtp.path(event.pathname), e))


    def process_IN_MOVED_TO(self, event):
        ftp = UploadFtp.connect()

        # check ingore
        if UploadFtp.ignore(event.pathname):
            synclogger.info("Ignore file: path=%s." % (event.pathname))
            return None

        if event.mask & pyinotify.IN_ISDIR:
            synclogger.debug("Renamed dir: from=%s, to=%s." % (event.src_pathname, event.pathname))

            # 去掉对源目录的监测
            try:
                wm.rm_watch(event.src_pathname, rec=True)
                synclogger.debug("Removed dir to watch list: path=%s." % (event.src_pathname))
            except Exception as e:
                synclogger.error("Removing dir to watch list failed: path=%s, error=%s." % (event.src_pathname, e))

            # 加入对新目录的监测
            try:
                wm.add_watch(event.pathname, mask, rec=True)
                synclogger.debug("Add new dir to watch list: %s." % (event.pathname))
            except Exception as e:
                synclogger.error("Add new dir to watch list failed: path=%s, error=%s." % (event.pathname, e))

            try:
                ftp.rename(UploadFtp.path(event.src_pathname), UploadFtp.path(event.pathname))
                synclogger.info("Renamed dir in server: from=%s, to=%s." % (UploadFtp.path(event.src_pathname), UploadFtp.path(event.pathname)))
            except Exception as e:
                synclogger.error("Renamed dir failed in server: from=%s, to=%s, error=%s." % (UploadFtp.path(event.src_pathname), UploadFtp.path(event.pathname), e))
        else:
            synclogger.debug("Renamed file: from=%s, to=%s." % (event.src_pathname, event.pathname))
            try:
                ftp.rename(UploadFtp.path(event.src_pathname), UploadFtp.path(event.pathname))
                synclogger.info("Renamed file in server: from=%s, to=%s." % (UploadFtp.path(event.src_pathname), UploadFtp.path(event.pathname)))
            except Exception as e:
                synclogger.error("Renamed file failed in server: from=%s, to=%s, error=%s." % (UploadFtp.path(event.src_pathname), UploadFtp.path(event.pathname), e))

def run():
    """执行程序"""
    handler = EventHandler()
    notifier = pyinotify.Notifier(wm, handler)

    # 遍历现有的子目录，加入监视
    for dirname, dirnames, filenames in os.walk(WATCH_PATH):
        synclogger.debug("Add new dir into watch list: %s." % (dirname))
        wm.add_watch(dirname, mask, rec=True)

    notifier.loop()

def daemon_start():
    """启动服务"""

    # do the UNIX double-fork magic, see Stevens' "Advanced
    # Programming in the UNIX Environment" for details (ISBN 0201563177)
    try:
        pid = os.fork()
        if pid > 0:
            # exit first parent
            sys.exit(0)
    except OSError, e:
        print >>sys.stderr, "fork #1 failed: %d (%s)." % (e.errno, e.strerror)
        sys.exit(1)
    # decouple from parent environment
    os.chdir("/")
    os.setsid()
    os.umask(0)
    # do second fork
    try:
        pid = os.fork()
        if pid > 0:
            # exit from second parent, print eventual PID before
            print >>sys.stdout, "Starting daemon PID %d." % pid
            synclogger.info("Starting daemon PID %d" % pid)
            open(PID_FILE,'w').write("%d"%pid)
            sys.exit(0)
    except OSError, e:
        print >>sys.stderr, "fork #2 failed: %d (%s)." % (e.errno, e.strerror)
        sys.exit(1)
    # start the daemon main loop
    run()

def daemon_stop():
    """停止服务"""
    if os.path.exists(PID_FILE):
        pid = open(PID_FILE, 'r').read()
        try:
            os.kill(int(pid), signal.SIGKILL)
            print >>sys.stderr, "Stop daemon PID %s success." % pid
            synclogger.info("Stop daemon PID %s success." % pid)
        except Exception as e:
            synclogger.error("Stopping daemon PID %s failed: %s." % (pid, e))
            print >>sys.stderr, "Stopping daemon PID %s failed: %s." % (pid, e)
        os.remove(PID_FILE)
        sys.exit(0)
    else:
        synclogger.error("Can't find PID file: path=%s." % PID_FILE)
        print >>sys.stderr, "Can't find PID file: path=%s." % PID_FILE

def usage():
    """使用帮助"""
    print "Help ..."

def main(argv):
    try:
        opts, args = getopt.getopt(argv, "", ["start", "stop"])
    except getopt.GetoptError:
        usage()
        sys.exit(2)

    for opt, arg in opts:
        # 启动或停止服务，否则输出使用帮助
        if opt == '--stop':
            daemon_stop()
        elif opt == '--start':
            daemon_start()
        else:
            usage()

if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exception:
        exc_type, exc_value, exc_traceback = sys.exc_info()
        if traceback.format_exception(exc_type, exc_value, exc_traceback):
            log_error = ''
            for line in traceback.format_exception(exc_type, exc_value, exc_traceback):
                log_error += line
        synclogger.error(log_error)