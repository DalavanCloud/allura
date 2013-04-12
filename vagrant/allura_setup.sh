#!/bin/bash

#       Licensed to the Apache Software Foundation (ASF) under one
#       or more contributor license agreements.  See the NOTICE file
#       distributed with this work for additional information
#       regarding copyright ownership.  The ASF licenses this file
#       to you under the Apache License, Version 2.0 (the
#       "License"); you may not use this file except in compliance
#       with the License.  You may obtain a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#       Unless required by applicable law or agreed to in writing,
#       software distributed under the License is distributed on an
#       "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
#       KIND, either express or implied.  See the License for the
#       specific language governing permissions and limitations
#       under the License.

# Install Solr
cd /home/vagrant/src
if [ ! -d apache-solr-1.4.1 ]
then
    echo "Installing Solr..."
    wget -nv http://archive.apache.org/dist/lucene/solr/1.4.1/apache-solr-1.4.1.tgz
    tar xf apache-solr-1.4.1.tgz && rm -f apache-solr-1.4.1.tgz
    cd apache-solr-1.4.1/example/
    mkdir -p /home/vagrant/src/allura/solr_config/conf
    cp solr/conf/solrconfig.xml /home/vagrant/src/allura/solr_config/conf/
    chown -R vagrant:vagrant /home/vagrant/src/apache-solr* /home/vagrant/src/allura/solr_config/conf/
fi

# Create log dir
if [ ! -d /var/log/allura ]
then
    sudo mkdir -p /var/log/allura
    sudo chown vagrant:vagrant /var/log/allura
fi

# Create startup script
if [ ! -f /home/vagrant/start_allura ]
then
    echo "Creating ~/start_allura script..."
    cp /vagrant/start_allura /home/vagrant
    chown vagrant:vagrant /home/vagrant/start_allura
fi

# Create .bash_profile with venv activation
if [ ! -f /home/vagrant/.bash_profile ]
then
    echo "Creating ~/.bash_profile ..."
    cp /home/vagrant/.profile /home/vagrant/.bash_profile
    echo -e "\n# Activate Allura virtualenv\n. /home/vagrant/anvil/bin/activate && cd /home/vagrant/src/allura" >> /home/vagrant/.bash_profile
    chown vagrant:vagrant /home/vagrant/.bash_profile
fi

# Setup Allura python packages
cd /home/vagrant/src/allura
sudo -u vagrant bash -c '. /home/vagrant/anvil/bin/activate; ./rebuild-all.bash'

echo "Purging unneeded packages..."
aptitude clean
aptitude -y -q purge ri
aptitude -y -q purge installation-report landscape-client landscape-common wireless-tools wpasupplicant
aptitude -y -q purge python-dbus libnl1 python-smartpm linux-headers-server python-twisted-core libiw30 language-selector-common
aptitude -y -q purge cloud-init juju python-twisted python-twisted-bin libdbus-glib-1-2 python-pexpect python-serial python-gobject python-pam accountsservice libaccountsservice0

echo "Zeroing free space to aid VM compression..."
cat /dev/zero > zero.fill;
echo "Errors about 'No space left' are ok; carrying on..."
sync;sleep 1;sync;rm -f zero.fill
dd if=/dev/zero of=/EMPTY bs=1M
rm -f /EMPTY
echo "Done with allura_setup.sh"

# sometimes mongo ends up stopped
# maybe due to that disk-filling exercise
# make sure it's still running
service mongodb status
if [ "$?" -ne "0" ]; then
	rm /var/lib/mongodb/mongod.lock
	service mongodb start
fi
