sync-inotify
============

之所以创建sync-inotify项目，主要是为了解决数据同步的问题。使用Linux内核的inotify特性，监视指定目录中的文件变化，并把文件的改变同步到指定的服务器上。

项目目前包含ftp-inotify.py，使用FTP协议将变化的内容进行同步。后续计划支持rsync、scp、webdav等其它协议。

如果要同步的内容经常出现改变，并要求较高的实时性，那么不建议使用本项目的脚本。

目前，本项目还没有在生产环境中部署和应用，如果你在部署中遇到问题，欢迎讨论。

运行环境
-------

本项目基于Python2.7.3开发，Python2.6.x不被支持，Python的安装位置为：/usr/bin/python2.7。

操作系统方面，使用Linux，内核 2.6.13 (June 18, 2005)。详细关于inotify的信息，参考[wikipedia](http://en.wikipedia.org/wiki/Inotify)。

执行sync-inotify中的脚本，需要[Pyinotify](https://github.com/seb-m/pyinotify)支持，因此需要先安装它。安装方法例如：
`shell> sudo pip install pyinotify`。

运行脚本
------

### ftp-inotify

#### 配置

##### IS_DEBUG

相对于关闭（False）调试模式，开启（True）后会有两个影响：
* 日志输出更加详细，可以用于发现执行过程中的细节问题；
* 日志文件由按日期轮询，改为 **daily.log.debug** 文件名；

##### PID_FILE

定义PID文件存储位置。默认在 **/var/run** 目录下建立和当前脚本同名的pid文件，例如：/var/run/ftp-inotify.pid。

一般情况下，除非特别需要，这个配置项无需修改。

##### LOG_FILE

定义日志文件存储路径。默认情况下，会创建 **/var/log/** 下创建和脚本同名目录，用来存储日志信息。

一般情况下，除非特别需要，这个配置项无需修改。

注意：当设置`LOG_FILE = None`时，并非关闭日志输出，而是直接将日志输出到终端上。

##### WATCH_PATH

监视文件变化的根路径。该配置项必须定义，否则脚本会报错退出。

##### UPLOAD_FTP_****

FTP服务器的相关信息。注意，该账号需要有创建、删除、重命名文件夹和文件的权限。该配置项必须定义，否则脚本会报错退出。

##### FILE_TYPES

允许同步的文件类型，使用逗号（,）分隔，可选值包括（[参考](http://www.iana.org/assignments/media-types)）：
* application
* audio
* example
* image
* message
* model
* multipart
* text
* video

如果不配置相关信息，则会同步所有文件。

##### FILE_EXTENSIONS

允许同步的文件扩展名，使用逗号（,）分隔，可选值例如：
* jpg
* jpeg
* png
* gif
* txt
* js
* css

注意：在判断文件类型的时候，**FILE_TYPES** 优先于 **FILE_EXTENSIONS** 。也就是说，只有检测文件类型失败时，才会使用扩展名进行检测。例如，在进行删除文件操作时，因为源文件已经被删除了，所以只能通过扩展名判断文件类型。

如果不配置相关信息，则会同步所有文件。

### 启动和停止

现有的ftp-inotify.py脚本，会以守护进程的方式运行（类似于服务）。

启动：`shell> ftp-inotify.py --start`<br/>
停止：`shell> ftp-inotify.py --stop`

如果要运行多个脚本，可以把脚本改名，然后分别启动。

### PID

默认的，脚本启动后，会在 **/var/run/** 下创建和脚本同名的pid文件。

例如使用ftp-inotify.py启动，那么PID文件就是：/var/run/ftp-inotify.pid。

### 日志文件

脚本会在 **/var/log/** 下创建和脚本同名目录，用来存储日志信息。根据 **IS_DEBUG** 的配置，文件名会有所不同。
* IS_DEBUG = True  : **/var/log/{PYTHON_FILENAME}/daily.debug**
* IS_DEBUG = False
    * 当日日志: **/var/log/{PYTHON_FILENAME}/daily.log**
    * 往日日志: **/var/log/{PYTHON_FILENAME}/daily.log.YYYY-MM-DD**

已知问题
-------

### 不支持使用-p参数一次性创建多级目录

例如：`shell> mkdir -p dir1/dir11`

遇到这样的操作，需要拆分成多条命令分别创建目录。


### 极短时间内，创建并删除目录

这种情况下，会导致错误输出但不会导致守护程序中断，终端会有类似下面提示：
> [2013-01-23 09:55:31,472 pyinotify ERROR] add_watch: cannot watch /tmp/the-test/dir5 WD=-1, Errno=No such file or directory (ENOENT)


### 极短时间内快速创建并删除文件

极短时间内快速创建并删除文件，会导致上传失败，因为当要上传文件的时候，源文件已经没有了。